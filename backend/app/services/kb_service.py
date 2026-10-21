"""知识库授权、文档存储及解析任务编排。"""
from __future__ import annotations
import hashlib
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from ..core.config import settings
from ..db.models import Citation, Chunk, DocStatus, Document, KnowledgeBase, ParseTask, TaskPhase, User, UserKbAccess
from ..engines.keyword_index import KeywordIndex
from ..engines.vector_store import vector_store
from ..schemas.kb import KbCreate, KbUpdate
from . import version_service as versions

ALLOWED_UPLOAD_EXTS = {'.pdf':'pdf', '.docx':'docx', '.txt':'txt', '.md':'md', '.markdown':'md', '.xlsx':'xlsx', '.csv':'csv', '.html':'html', '.htm':'html', '.png':'image', '.jpg':'image', '.jpeg':'image', '.bmp':'image'}
MAX_FILE_SIZE = 50 * 1024 * 1024
_task_lock = threading.Lock()

def now_utc_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def can_manage_kb(user: User | None, kb: KnowledgeBase) -> bool:
    """System roles never confer ownership of someone else's papers."""
    return bool(user and user.is_active and kb.owner_id == user.id)

def ensure_kb_access(db: Session, user: User | None, kb: KnowledgeBase | None, *, write=False, allow_disabled=False):
    if user is None or not user.is_active:
        raise versions.VersionError(401, '账号未登录或已停用')
    if kb is None:
        raise versions.VersionError(404, '知识库不存在')
    owner = can_manage_kb(user, kb)
    if write:
        if not owner:
            raise versions.VersionError(403, '仅知识库所有者可以维护资料与共享权限')
    elif not owner and db.scalar(select(UserKbAccess.id).where(
            UserKbAccess.kb_id == kb.id, UserKbAccess.role_code == user.role_code).limit(1)) is None:
        raise versions.VersionError(403, '无权访问该知识库')
    if not allow_disabled and kb.status != 'enabled':
        raise versions.VersionError(409, '知识库已停用，请先启用后再访问资料')
    return kb

def _invalidate_retrieval(kb_id):
    KeywordIndex.invalidate(kb_id)
    vector_store.invalidate(kb_id)

def _checked_upload_path(kb_id: int, stored_path: str, *, must_exist: bool) -> Path:
    """Validate both the lexical library root and the resolved file identity.

    No symlink/junction below upload_dir is followed, even when it points into
    another library within upload_dir. Database paths are not trusted authority.
    """
    if not stored_path:
        raise FileNotFoundError('文档原文件不存在')
    try:
        base = settings.upload_dir.absolute()
        candidate = Path(stored_path)
        if not candidate.is_absolute():
            candidate = settings.root_dir / candidate
        candidate = candidate.absolute()
        relative = candidate.relative_to(base)
        if len(relative.parts) < 2 or relative.parts[0] != str(kb_id) or '..' in relative.parts:
            raise ValueError('wrong library directory')
        component = base
        for part in ('', *relative.parts):
            component = component / part
            if component.is_symlink() or component.is_junction():
                raise ValueError('linked upload component')
        resolved = candidate.resolve()
        library_root = base.resolve() / str(kb_id)
        if not resolved.is_relative_to(library_root):
            raise ValueError('resolved path outside library')
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError('文档路径不在本知识库受控上传目录内，或包含不安全链接') from error
    if must_exist and not resolved.is_file():
        raise FileNotFoundError('文档原文件不存在')
    return resolved

def document_file_path(doc: Document, *, must_exist=True) -> Path:
    return _checked_upload_path(doc.kb_id, doc.storage_path, must_exist=must_exist)

def create_kb(db: Session, operator: User, data: KbCreate, import_samples: bool = False):
    if operator is None or not operator.is_active:
        raise versions.VersionError(401, '账号未登录或已停用')
    # Retired request fields are accepted for compatibility but cannot bind an
    # industry prompt, import demo files or broaden an empty sharing selection.
    kb = KnowledgeBase(name=data.name, description=data.description, template_id=None, status='enabled', owner_id=operator.id)
    db.add(kb)
    db.flush()
    for code in dict.fromkeys(data.role_codes):
        db.add(UserKbAccess(kb_id=kb.id, role_code=code))
    db.commit()
    db.refresh(kb)
    return kb

def update_kb(db: Session, kb: KnowledgeBase, data: KbUpdate, *, actor: User):
    ensure_kb_access(db, actor, kb, write=True, allow_disabled=True)
    for field, value in data.model_dump(exclude_unset=True).items():
        if field != 'template_id' and value is not None:
            setattr(kb, field, value)
    db.commit()
    db.refresh(kb)
    _invalidate_retrieval(kb.id)
    return kb

def grant_roles(db: Session, kb: KnowledgeBase, role_codes: list[str], *, actor: User):
    ensure_kb_access(db, actor, kb, write=True, allow_disabled=True)
    db.query(UserKbAccess).filter(UserKbAccess.kb_id == kb.id).delete()
    for code in dict.fromkeys(role_codes):
        db.add(UserKbAccess(kb_id=kb.id, role_code=code))
    db.commit()
    _invalidate_retrieval(kb.id)

def kb_stats(db: Session, kb_id: int, actor: User | None = None):
    ensure_kb_access(db, actor, db.get(KnowledgeBase, kb_id))
    listing = versions.document_listing_filter(db, actor)
    visible_docs = select(Document.id).where(Document.kb_id == kb_id, listing)
    counts = dict(db.execute(select(Document.status, func.count(Document.id)).where(Document.kb_id == kb_id, listing).group_by(Document.status)).all())
    pending = db.scalar(select(func.count(func.distinct(ParseTask.document_id))).where(ParseTask.document_id.in_(visible_docs), ParseTask.phase.notin_(['success','failed']))) or 0
    return dict(kb_id=kb_id, doc_count=sum(counts.values()), chunk_count=db.scalar(select(func.count(Chunk.id)).where(Chunk.kb_id == kb_id, Chunk.document_id.in_(visible_docs), versions.current_chunk_filter(db))) or 0,
                success_docs=counts.get('success', 0), failed_docs=counts.get('failed', 0),
                pending_docs=pending if versions.versioning_enabled(db) else counts.get('pending',0)+counts.get('parsing',0),
                last_upload_at=db.scalar(select(func.max(Document.created_at)).where(Document.kb_id == kb_id, listing)))

def _safe_stem(filename: str):
    return (re.sub(r'[^\w\u4e00-\u9fff-]+', '_', Path(filename).stem) or 'doc')[:80]

def save_upload(db: Session, kb: KnowledgeBase, operator: User, filename: str, content: bytes, *, series_id=None, version_label=None, expected_series_revision=None, supersedes_id=None, commit_changes=True):
    ensure_kb_access(db, operator, kb, write=True)
    filename = Path(filename.replace('\\', '/')).name.strip()
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTS:
        raise ValueError(f'不支持的文件类型：{ext or "无扩展名"}；旧版 .xls 请另存为 .xlsx')
    if not content:
        raise ValueError('不能上传空文件')
    if len(content) > MAX_FILE_SIZE:
        raise ValueError('文件超过 50 MB 上限')
    if len(filename) > 255 or any(ord(c) < 32 for c in filename):
        raise ValueError('文件名过长或含无效字符')
    # 保留既有 SHA-1 指纹格式以兼容原数据；仅用于重复内容检查，不作为安全签名。
    fingerprint = hashlib.sha1(content).hexdigest()
    managed = versions.versioning_enabled(db)
    sha256 = hashlib.sha256(content).hexdigest()
    if managed:
        from ..db.models import DocumentVersion
        duplicate = db.scalar(select(Document).join(DocumentVersion, DocumentVersion.version_id == Document.id).where(Document.kb_id == kb.id, DocumentVersion.sha256 == sha256).order_by(Document.id).limit(1))
    else:
        duplicate = db.scalar(select(Document).where(Document.kb_id == kb.id, Document.fingerprint == fingerprint))
    if duplicate:
        return duplicate, False
    directory = settings.upload_dir / str(kb.id)
    target = _checked_upload_path(kb.id, str(directory / f'{_safe_stem(filename)}_{uuid.uuid4().hex}{ext}'), must_exist=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as handle:
        handle.write(content)
    storage_path = str(target.relative_to(settings.root_dir)) if target.is_relative_to(settings.root_dir) else str(target)
    doc = Document(kb_id=kb.id, filename=filename, file_type=ALLOWED_UPLOAD_EXTS[ext], file_size=len(content), storage_path=storage_path, fingerprint=fingerprint, status=DocStatus.PENDING, uploaded_by=operator.id)
    try:
        db.add(doc)
        db.flush()
        if managed:
            versions.register_uploaded_document(db, operator, doc, sha256, series_id=series_id, version_label=version_label, expected_series_revision=expected_series_revision, supersedes_id=supersedes_id)
        if commit_changes:
            db.commit()
            db.refresh(doc)
    except Exception:
        db.rollback()
        # A commit acknowledgement failure must never remove a persisted file.
        try:
            persisted = db.scalar(select(Document.id).where(Document.storage_path == storage_path).limit(1)) is not None
        except Exception:
            persisted = True
        if not persisted and target.is_relative_to(settings.upload_dir.resolve()):
            target.unlink(missing_ok=True)
        raise
    return doc, True

def create_parse_task(db: Session, doc: Document, operator: User, *, commit_changes=True):
    ensure_kb_access(db, operator, db.get(KnowledgeBase, doc.kb_id), write=True)
    try:
        document_file_path(doc)
    except FileNotFoundError as error:
        raise versions.VersionError(409, '文档原文件不存在，无法重新解析') from error
    managed = versions.versioning_enabled(db)
    with _task_lock:
        existing = db.scalar(select(ParseTask).where(ParseTask.document_id == doc.id, ParseTask.phase.notin_(['success','failed'])))
        if existing:
            raise ValueError('该文档已有进行中的解析任务，请勿重复提交')
        previous = versions.current_revision(db, doc.id) if managed else None
        task = ParseTask(document_id=doc.id, phase=TaskPhase.QUEUED, progress=0, operator_id=operator.id)
        doc.status, doc.error_message = (DocStatus.SUCCESS if previous else DocStatus.PENDING), ''
        try:
            db.add(task)
            db.flush()
            if managed:
                versions.register_parse_task(db, doc, task)
            if commit_changes:
                db.commit()
                db.refresh(task)
        except IntegrityError as error:
            db.rollback()
            raise versions.VersionError(409, '该文档已有进行中的解析任务，请重新读取状态') from error
    _invalidate_retrieval(doc.kb_id)
    return task

def delete_doc(db: Session, doc: Document, operator: User | None = None):
    ensure_kb_access(db, operator, db.get(KnowledgeBase, doc.kb_id), write=True)
    active = db.scalar(select(ParseTask.id).where(ParseTask.document_id == doc.id, ParseTask.phase.notin_(['success','failed'])))
    if active:
        raise ValueError('文档正在解析，请等待任务结束后删除')
    if versions.versioning_enabled(db):
        from ..db.models import DocumentVersion
        version = db.get(DocumentVersion, doc.id)
        if version is None:
            raise versions.VersionError(409, '资料缺少版本元数据，拒绝物理删除')
        return versions.archive_version(db, operator, version_id=doc.id, expected_revision=version.metadata_revision, reason='兼容删除入口：实际归档，保留原文件与历史证据')
    target = document_file_path(doc, must_exist=False) if doc.storage_path else None
    # 历史引用是快照；取消旧片段关联，避免 SQLite 重用 ID 后引用到其他内容。
    ids = select(Chunk.id).where(Chunk.document_id == doc.id)
    db.query(Citation).filter(Citation.chunk_id.in_(ids)).update({Citation.chunk_id:None}, synchronize_session=False)
    db.query(Chunk).filter(Chunk.document_id == doc.id).delete(synchronize_session=False)
    db.query(ParseTask).filter(ParseTask.document_id == doc.id).delete(synchronize_session=False)
    kb_id = doc.kb_id
    db.delete(doc)
    db.commit()
    _invalidate_retrieval(kb_id)
    if target:
        target.unlink(missing_ok=True)

def get_visible_kb_ids(db: Session, user: User):
    if not user or not user.is_active:
        return []
    granted = select(UserKbAccess.kb_id).where(UserKbAccess.role_code == user.role_code)
    return list(db.scalars(select(KnowledgeBase.id).where(or_(KnowledgeBase.owner_id == user.id, KnowledgeBase.id.in_(granted)))))

