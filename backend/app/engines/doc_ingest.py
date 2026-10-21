"""有界后台入库使用的解析流水线；任何失败都写入可见终态。"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from ..core.config import settings
from ..db.models import Citation, Chunk, DocStatus, Document, ParseTask, TaskPhase
from ..db.session import get_session_factory
from .chunker import DEFAULT_MAX_CHARS, chunk_parts
from .doc_parser import ParseError, parse_file
from .embedder import embed_texts, get_embedding_identity
from .academic_retrieval import annotate_for_kb
from .keyword_index import KeywordIndex
from .vector_store import encode_vector_json, l2_normalize, vector_store

class DocIngestError(RuntimeError):
    pass

def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def _run_extract(doc_path: Path, file_type: str):
    return parse_file(doc_path, file_type)

def _clear_old_chunks(db, document_id: int) -> None:
    ids = select(Chunk.id).where(Chunk.document_id == document_id)
    db.query(Citation).filter(Citation.chunk_id.in_(ids)).update({Citation.chunk_id: None}, synchronize_session=False)
    db.query(Chunk).filter(Chunk.document_id == document_id).delete(synchronize_session=False)

def ingest_document(task_id: int) -> dict:
    factory = get_session_factory()
    from ..services import version_service
    with factory() as check_db:
        managed = version_service.versioning_enabled(check_db)
    if managed:
        from .versioned_ingest import ingest_versioned
        return ingest_versioned(task_id, factory=factory)
    kb_id = None
    try:
        with factory() as db:
            task = db.get(ParseTask, task_id)
            if not task:
                raise DocIngestError(f'任务不存在: {task_id}')
            if task.phase in (TaskPhase.SUCCESS, TaskPhase.FAILED):
                return {'status': task.phase, 'chunk_count': task.chunk_count}
            doc = db.get(Document, task.document_id)
            if not doc:
                raise DocIngestError('文档已不存在')
            kb_id = doc.kb_id
            abs_path = (settings.root_dir / doc.storage_path).resolve()
            if not abs_path.is_relative_to(settings.upload_dir.resolve()):
                raise DocIngestError('文件路径不在上传目录内')
            file_type = doc.file_type
            task.phase, task.progress, task.started_at = TaskPhase.EXTRACTING, 10, _now()
            task.error_message = ''
            doc.status, doc.error_message = DocStatus.PARSING, ''
            db.commit()
        KeywordIndex.invalidate(kb_id)
        vector_store.invalidate(kb_id)
        parts = _run_extract(abs_path, file_type)
        with factory() as db:
            task = db.get(ParseTask, task_id)
            task.phase, task.progress = TaskPhase.CHUNKING, 45
            db.commit()
        chunks = chunk_parts(parts, max_chars=DEFAULT_MAX_CHARS)
        annotate_for_kb(kb_id, parts, chunks, file_type)
        if not chunks or not any(c['content'].strip() for c in chunks):
            raise ParseError('未提取到可检索文本；扫描 PDF 请先转换为含文字层的文件，图片请配置本地 OCR')
        with factory() as db:
            task = db.get(ParseTask, task_id)
            task.phase, task.progress = TaskPhase.VECTORIZING, 70
            db.commit()
        model_id = get_embedding_identity()
        vectors = embed_texts([c['content'] for c in chunks])
        vectorized = vectors is not None and len(vectors) == len(chunks) and all(len(v) > 0 for v in vectors)
        if vectorized and get_embedding_identity() != model_id:
            raise DocIngestError('Embedding 模型在入库编码期间发生变化，请重试')
        with factory() as db:
            task = db.get(ParseTask, task_id)
            doc = db.get(Document, task.document_id)
            _clear_old_chunks(db, doc.id)
            for idx, chunk in enumerate(chunks):
                meta = chunk.get('meta', {})
                if vectorized and model_id is not None:
                    meta = {**meta, 'embedding_model_id': model_id}
                db.add(Chunk(
                    document_id=doc.id, kb_id=doc.kb_id, seq_no=idx + 1,
                    content=chunk['content'], page_no=chunk.get('page_no', 0),
                    meta_json=json.dumps(meta, ensure_ascii=False),
                    chunk_hash=hashlib.sha256(chunk['content'].encode()).hexdigest(),
                    vector_json=encode_vector_json(l2_normalize(vectors[idx])) if vectorized else None,
                ))
            task.chunk_count = len(chunks)
            task.phase, task.progress, task.finished_at = TaskPhase.SUCCESS, 100, _now()
            task.error_message = ''
            doc.status, doc.error_message = DocStatus.SUCCESS, ''
            doc.page_count = max((c.get('page_no', 0) or 0 for c in chunks), default=0)
            db.commit()
        KeywordIndex.invalidate(kb_id)
        vector_store.invalidate(kb_id)
        return {'status': 'success', 'chunk_count': len(chunks), 'vectorized': vectorized}
    except Exception as exc:
        reason = str(exc)[:500] if isinstance(exc, (ParseError, DocIngestError)) else f'解析异常（{type(exc).__name__}），请检查文件是否损坏或格式是否受支持'
        with factory() as db:
            task = db.get(ParseTask, task_id)
            if task:
                task.phase, task.error_message, task.finished_at = TaskPhase.FAILED, reason, _now()
                doc = db.get(Document, task.document_id)
                if doc:
                    kb_id = doc.kb_id
                    doc.status, doc.error_message = DocStatus.FAILED, reason
                db.commit()
        KeywordIndex.invalidate(kb_id)
        vector_store.invalidate(kb_id)
        return {'status': 'failed', 'error': reason}
