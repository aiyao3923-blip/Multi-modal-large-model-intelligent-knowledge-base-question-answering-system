"""Revision-based ingestion: build new evidence, then atomically switch current.

The last successful revision remains readable while a reparse runs or fails.
Neither old chunks nor citation foreign keys are cleared in versioned mode.
"""
from __future__ import annotations
import hashlib
import json
import math
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from sqlalchemy import select, update
from ..db.models import AuditEvent, Chunk, Document, DocumentVersion, ParseRevision, ParseTask, RevisionChunk
from ..db.session import get_session_factory
from ..services import version_service as versions
from .chunker import DEFAULT_MAX_CHARS, chunk_parts
from .doc_parser import ParseError, parse_file
from .embedder import embed_texts, get_embedding_identity
from .academic_retrieval import annotate_for_kb
from .keyword_index import KeywordIndex
from .vector_store import encode_vector_json, l2_normalize, vector_store

class VersionedIngestError(RuntimeError):
    pass

def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def _parser_build():
    package_versions = {}
    for name in ('pypdf', 'python-docx', 'openpyxl', 'beautifulsoup4'):
        try:
            package_versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            package_versions[name] = 'unavailable'
    digest = hashlib.sha256(versions.json_text(package_versions).encode('utf-8'))
    files = [Path(__file__), Path(__file__).with_name('chunker.py'), *sorted(Path(__file__).with_name('doc_parser').glob('*.py'))]
    for path in files:
        digest.update(path.name.encode('utf-8')); digest.update(path.read_bytes())
    return 'builtin-' + digest.hexdigest()[:24], package_versions

def _terminal(task):
    data = {'status': task.phase, 'chunk_count': task.chunk_count}
    if task.phase == 'failed':
        data['error'] = task.error_message
    return data

def fail_versioned_task(task_id, reason, *, factory=None):
    factory = factory or get_session_factory()
    kb_id = None
    with factory() as db:
        task = db.get(ParseTask, task_id)
        if task is None:
            return {'status': 'failed', 'error': reason}
        if task.phase in ('success', 'failed'):
            return _terminal(task)
        doc = db.get(Document, task.document_id)
        revision = db.scalar(select(ParseRevision).where(ParseRevision.task_id == task_id))
        if revision is not None and revision.state == 'building':
            revision.state = 'failed'; revision.error_message = reason; revision.finished_at = _now()
        task.phase = 'failed'; task.error_message = reason; task.finished_at = _now()
        if doc is not None:
            kb_id = doc.kb_id
            previous = versions.current_revision(db, doc.id)
            doc.status = 'success' if previous is not None else 'failed'
            doc.error_message = reason
            db.add(AuditEvent(actor_id=task.operator_id, kb_id=doc.kb_id, object_type='parse_revision', object_id=revision.id if revision else None, action='parse_failed', reason=reason, before_json='{}', after_json=versions.json_text({'task_id': task_id, 'retained_current_revision': previous.id if previous else None})))
        db.commit()
    if kb_id is not None:
        KeywordIndex.invalidate(kb_id)
        vector_store.invalidate(kb_id)
    return {'status': 'failed', 'error': reason}

def ingest_versioned(task_id: int, *, factory=None) -> dict:
    factory = factory or get_session_factory()
    kb_id = None
    try:
        with factory() as db:
            versions.require_ready(db)
            task = db.get(ParseTask, task_id)
            if task is None:
                raise VersionedIngestError('解析任务不存在')
            if task.phase in ('success', 'failed'):
                return _terminal(task)
            if task.phase != 'queued':
                return {'status': 'running', 'chunk_count': task.chunk_count}
            doc = db.get(Document, task.document_id)
            version = db.get(DocumentVersion, task.document_id)
            revision = db.scalar(select(ParseRevision).where(ParseRevision.task_id == task_id))
            if doc is None or version is None or revision is None or revision.state != 'building':
                raise VersionedIngestError('任务缺少完整版本/解析修订，拒绝退回覆盖式解析')
            claim = db.execute(update(ParseTask).where(ParseTask.id == task_id, ParseTask.phase == 'queued').values(phase='extracting', progress=10, started_at=_now(), error_message='').execution_options(synchronize_session=False))
            if claim.rowcount != 1:
                db.rollback()
                return {'status': 'running', 'chunk_count': 0}
            previous = versions.current_revision(db, doc.id)
            doc.status = 'success' if previous is not None else 'parsing'
            doc.error_message = ''
            kb_id, version_id, revision_id, file_type = doc.kb_id, doc.id, revision.id, doc.file_type
            db.commit()
        with factory() as db:
            doc, version = db.get(Document, version_id), db.get(DocumentVersion, version_id)
            source = versions.verify_source_file(doc, version)
            build, packages = _parser_build()
            revision = db.get(ParseRevision, revision_id)
            revision.file_sha256 = version.sha256
            revision.source_integrity = 'verified'
            revision.parser_version = build
            db.commit()
        parts = parse_file(source, file_type)
        with factory() as db:
            task = db.get(ParseTask, task_id)
            task.phase, task.progress = 'chunking', 45
            db.commit()
        chunks = chunk_parts(parts, max_chars=DEFAULT_MAX_CHARS)
        annotate_for_kb(kb_id, parts, chunks, file_type)
        if not chunks or not any(chunk['content'].strip() for chunk in chunks):
            raise ParseError('未提取到可检索文本；请核对原文件，扫描件需要已配置的本地OCR')
        with factory() as db:
            task = db.get(ParseTask, task_id)
            task.phase, task.progress = 'vectorizing', 70
            db.commit()
        model_id = get_embedding_identity()
        vectors = embed_texts([chunk['content'] for chunk in chunks])
        vectorized = bool(vectors is not None and len(vectors) == len(chunks) and len(vectors[0]) > 0 and all(len(v) == len(vectors[0]) and all(isinstance(x, (int,float)) and not isinstance(x, bool) and math.isfinite(x) for x in v) for v in vectors))
        if vectorized and get_embedding_identity() != model_id:
            raise VersionedIngestError('Embedding 模型在入库编码期间发生变化，请重试')
        encoded = [encode_vector_json(l2_normalize(vector)) for vector in vectors] if vectorized else [None] * len(chunks)
        with factory() as db:
            task = db.get(ParseTask, task_id)
            doc, version = db.get(Document, version_id), db.get(DocumentVersion, version_id)
            revision = db.get(ParseRevision, revision_id)
            if task.phase == 'success':
                return _terminal(task)
            if revision.state != 'building':
                raise VersionedIngestError('解析修订已结束，不能重复写入')
            versions.verify_source_file(doc, version)
            payloads = []
            for index, item in enumerate(chunks):
                content = item['content']
                meta = item.get('meta', {})
                if vectorized and model_id is not None:
                    meta = {**meta, 'embedding_model_id': model_id}
                chunk = Chunk(document_id=doc.id, kb_id=doc.kb_id, seq_no=index+1, content=content, page_no=item.get('page_no',0) or 0, meta_json=json.dumps(meta, ensure_ascii=False), chunk_hash=hashlib.sha256(content.encode('utf-8')).hexdigest(), vector_json=encoded[index])
                db.add(chunk); db.flush()
                locator = {'kind':'parsed_fragment', 'reliability':'parsed_fragment', 'seq_no':index+1, 'page_no':chunk.page_no, 'parser_meta':item.get('meta',{})}
                db.add(RevisionChunk(chunk_id=chunk.id, parse_revision_id=revision.id, content_sha256=chunk.chunk_hash, locator_json=versions.json_text(locator)))
                payloads.append({'seq_no':index+1, 'content':content, 'page_no':chunk.page_no, 'meta':meta, 'vector_json':encoded[index]})
            # Memberships must be written while building; successful revisions
            # reject even append-only changes to their chunk set.
            db.flush()
            previous = db.scalar(select(ParseRevision).where(ParseRevision.version_id == doc.id, ParseRevision.is_current.is_(True)))
            if previous is not None:
                previous.is_current = False
                db.flush()
            revision.content_hash = hashlib.sha256(versions.json_text(payloads).encode('utf-8')).hexdigest()
            revision.chunk_count = len(chunks)
            revision.state = 'success'; revision.is_current = True; revision.finished_at = _now(); revision.error_message = ''
            task.phase, task.progress, task.chunk_count, task.error_message, task.finished_at = 'success', 100, len(chunks), '', _now()
            doc.status, doc.error_message = 'success', ''
            doc.page_count = max((chunk.get('page_no',0) or 0 for chunk in chunks), default=0)
            doc.updated_at = _now()
            db.add(AuditEvent(actor_id=task.operator_id, kb_id=doc.kb_id, object_type='parse_revision', object_id=revision.id, action='parse_current_switched', reason='新修订全部构建成功后原子切换；旧修订和引用保留', before_json=versions.json_text({'current_revision_id': previous.id if previous else None}), after_json=versions.json_text({'current_revision_id': revision.id, 'chunk_count':len(chunks), 'vectorized':vectorized, 'parser_packages':packages})))
            db.commit()
        KeywordIndex.invalidate(kb_id)
        vector_store.invalidate(kb_id)
        return {'status':'success', 'chunk_count':len(chunks), 'vectorized':vectorized, 'parse_revision_id':revision_id}
    except Exception as error:
        reason = str(error)[:500] if isinstance(error, (ParseError, VersionedIngestError, versions.VersionError)) else '解析异常（' + type(error).__name__ + '），请核对文件或处理记录'
        return fail_versioned_task(task_id, reason, factory=factory)
