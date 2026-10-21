"""知识库管理 API：授权范围内的批量统计、分页文档和解析任务。"""
from __future__ import annotations
import base64
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from ..api.deps import CurrentUser, get_current_user
from ..db.models import Chunk, Document, IndustryTemplate, KnowledgeBase, ParseTask, User, UserKbAccess
from ..db.session import get_db
from ..schemas.common import MsgOut, PageOut
from ..schemas.kb import ChunkOut, DocOut, DocPageOut, KbCreate, KbGrantReq, KbOut, KbStatsOut, KbUpdate, TaskOut
from ..schemas.versions import VersionOut
from ..services import kb_service, version_service as versions
from ..workers.tasks import submit_parse
router = APIRouter(prefix='/api', tags=['knowledge'])

def _visible(db, current):
    return kb_service.get_visible_kb_ids(db, db.get(User, current.id))

def _load_kb(db, kb_id, current, *, write=False, content=False):
    return kb_service.ensure_kb_access(db, db.get(User, current.id), db.get(KnowledgeBase, kb_id),
                                      write=write, allow_disabled=not content)

def _load_document(db, kb_id, doc_id, current, *, write=False):
    _load_kb(db, kb_id, current, write=write, content=True)
    doc = db.get(Document, doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(404, '文档不存在')
    if not write:
        versions.ensure_document_readable(db, db.get(User, current.id), doc)
    return doc

def _kb_outputs(db, kbs, actor=None):
    if not kbs:
        return []
    ids = [k.id for k in kbs]
    visible_docs = select(Document.id).where(Document.kb_id.in_(ids), versions.document_listing_filter(db, actor))
    docs = dict(db.execute(select(Document.kb_id, func.count(Document.id)).where(Document.id.in_(visible_docs)).group_by(Document.kb_id)).all())
    chunks = dict(db.execute(select(Chunk.kb_id, func.count(Chunk.id)).join(Document, Document.id == Chunk.document_id).where(Chunk.kb_id == Document.kb_id, Chunk.document_id.in_(visible_docs), versions.current_chunk_filter(db)).group_by(Chunk.kb_id)).all())
    roles = {}
    for kid, role in db.execute(select(UserKbAccess.kb_id, UserKbAccess.role_code).where(UserKbAccess.kb_id.in_(ids)).order_by(UserKbAccess.id)):
        roles.setdefault(kid, []).append(role)
    template_ids = {k.template_id for k in kbs if k.template_id}
    templates = dict(db.execute(select(IndustryTemplate.id, IndustryTemplate.name).where(IndustryTemplate.id.in_(template_ids))).all()) if template_ids else {}
    result = []
    for kb in kbs:
        out = KbOut.model_validate(kb)
        out.can_manage = kb_service.can_manage_kb(actor, kb)
        out.doc_count, out.chunk_count = docs.get(kb.id, 0), chunks.get(kb.id, 0)
        out.granted_roles, out.template_name = roles.get(kb.id, []), templates.get(kb.template_id, '')
        result.append(out)
    return result

def _to_kb_out(db, kb, actor=None):
    return _kb_outputs(db, [kb], actor)[0]

@router.get('/templates', deprecated=True)
def list_templates(_: CurrentUser = Depends(get_current_user)):
    raise HTTPException(410, '行业模板已退休；请创建私人知识库并上传自己的资料')

@router.get('/kbs', response_model=list[KbOut])
def list_kbs(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    actor = db.get(User, current.id)
    visible = kb_service.get_visible_kb_ids(db, actor)
    rows = db.scalars(select(KnowledgeBase).where(KnowledgeBase.id.in_(visible)).order_by(KnowledgeBase.id)).all()
    return _kb_outputs(db, rows, actor)

@router.post('/kbs', response_model=KbOut)
def create_kb(data: KbCreate, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    if db.scalar(select(KnowledgeBase.id).where(KnowledgeBase.name == data.name)):
        raise HTTPException(409, '知识库名称已存在')
    actor = db.get(User, current.id)
    return _to_kb_out(db, kb_service.create_kb(db, actor, data), actor)

@router.get('/kbs/{kb_id}', response_model=KbOut)
def get_kb(kb_id: int, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    return _to_kb_out(db, _load_kb(db, kb_id, current), db.get(User, current.id))

@router.patch('/kbs/{kb_id}', response_model=KbOut)
def patch_kb(kb_id: int, data: KbUpdate, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    kb = _load_kb(db, kb_id, current, write=True)
    if data.name and db.scalar(select(KnowledgeBase.id).where(KnowledgeBase.name == data.name, KnowledgeBase.id != kb.id)):
        raise HTTPException(409, '知识库名称已存在')
    actor = db.get(User, current.id)
    return _to_kb_out(db, kb_service.update_kb(db, kb, data, actor=actor), actor)

@router.post('/kbs/{kb_id}/grant', response_model=MsgOut)
def grant_kb(kb_id: int, req: KbGrantReq, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    kb_service.grant_roles(db, _load_kb(db, kb_id, current, write=True), req.role_codes, actor=db.get(User, current.id))
    return MsgOut(message='授权已更新')

@router.get('/kbs/{kb_id}/stats', response_model=KbStatsOut)
def get_kb_stats(kb_id: int, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    _load_kb(db, kb_id, current, content=True)
    return KbStatsOut(**kb_service.kb_stats(db, kb_id, db.get(User, current.id)))

@router.post('/kbs/{kb_id}/docs', response_model=DocOut)
async def upload_doc(kb_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    kb = _load_kb(db, kb_id, current, write=True, content=True)
    content = await file.read(kb_service.MAX_FILE_SIZE + 1)
    await file.close()
    actor = db.get(User, current.id)
    managed = versions.versioning_enabled(db)
    try:
        doc, is_new = kb_service.save_upload(db, kb, actor, file.filename or 'untitled', content)
        if not is_new:
            detail = {'message':'同库已存在相同内容，未创建新版本', 'existing_version_id':doc.id, 'is_new':False, 'duplicate':True} if managed else f'该文档内容已存在：{doc.filename}'
            raise HTTPException(409, detail)
        task = kb_service.create_parse_task(db, doc, actor)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    submit_parse(task.id)
    db.refresh(doc)
    out = DocOut.model_validate(doc)
    out.is_new = True
    if managed:
        out.version = VersionOut.model_validate(versions.version_detail(db, actor, doc.id))
    return out

@router.get('/kbs/{kb_id}/docs', response_model=DocPageOut)
def list_docs(kb_id: int, page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100), q: str = Query('', max_length=200), status: Literal['pending','parsing','success','failed'] | None = None, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    _load_kb(db, kb_id, current, content=True)
    actor = db.get(User, current.id)
    managed = versions.versioning_enabled(db)
    filters = [Document.kb_id == kb_id, versions.document_listing_filter(db, actor)]
    if q.strip():
        filters.append(Document.filename.contains(q.strip(), autoescape=True))
    if status:
        filters.append(Document.status == status)
    total = db.scalar(select(func.count(Document.id)).where(*filters)) or 0
    docs = db.scalars(select(Document).where(*filters).order_by(Document.id.desc()).offset((page-1)*size).limit(size)).all()
    counts = dict(db.execute(select(Chunk.document_id, func.count(Chunk.id)).where(Chunk.kb_id == kb_id, Chunk.document_id.in_([d.id for d in docs]), versions.current_chunk_filter(db)).group_by(Chunk.document_id)).all()) if docs else {}
    version_map = versions.summaries_for_documents(db, actor, docs) if managed else {}
    items = []
    for doc in docs:
        out = DocOut.model_validate(doc)
        out.chunk_count = counts.get(doc.id, 0)
        out.version = VersionOut.model_validate(version_map[doc.id]) if doc.id in version_map else None
        items.append(out)
    return DocPageOut(items=items, total=total, page=page, size=size, versioning_enabled=managed)

@router.delete('/kbs/{kb_id}/docs/{doc_id}', response_model=MsgOut)
def delete_doc(kb_id: int, doc_id: int, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    doc = _load_document(db, kb_id, doc_id, current, write=True)
    try:
        managed = versions.versioning_enabled(db)
        kb_service.delete_doc(db, doc, db.get(User, current.id))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return MsgOut(message='版本已归档，原文件和历史修订保留' if managed else '文档已删除，历史引用快照保留')

@router.post('/kbs/{kb_id}/docs/{doc_id}/reparse', response_model=MsgOut)
def reparse_doc(kb_id: int, doc_id: int, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    doc = _load_document(db, kb_id, doc_id, current, write=True)
    try:
        task = kb_service.create_parse_task(db, doc, db.get(User, current.id))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    submit_parse(task.id)
    return MsgOut(message='已加入解析队列')

def _task_query(db, current, kb_id=None, phase=None, q=''):
    visible = _visible(db, current)
    if kb_id is not None:
        _load_kb(db, kb_id, current, content=True)
    query = select(ParseTask, Document.filename, Document.kb_id, KnowledgeBase.name).join(Document, Document.id == ParseTask.document_id).join(KnowledgeBase, KnowledgeBase.id == Document.kb_id).where(Document.kb_id.in_(visible), KnowledgeBase.status == 'enabled', versions.document_listing_filter(db, db.get(User, current.id)))
    if kb_id is not None:
        query = query.where(Document.kb_id == kb_id)
    if phase:
        query = query.where(ParseTask.phase == phase)
    if q.strip():
        query = query.where(Document.filename.contains(q.strip(), autoescape=True))
    return query

def _task_outputs(rows):
    items = []
    for task, name, kb_id, kb_name in rows:
        out = TaskOut.model_validate(task)
        out.filename, out.kb_id, out.kb_name = name, kb_id, kb_name
        items.append(out)
    return items

@router.get('/tasks/page', response_model=PageOut[TaskOut])
def tasks_page(kb_id: int | None = None, phase: Literal['queued','extracting','chunking','vectorizing','success','failed'] | None = None, q: str = Query('', max_length=200), page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    query = _task_query(db, current, kb_id, phase, q)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(query.order_by(ParseTask.id.desc()).offset((page-1)*size).limit(size)).all()
    return PageOut(items=_task_outputs(rows), total=total, page=page, size=size)

@router.get('/tasks', response_model=list[TaskOut])
def list_tasks(kb_id: int | None = None, limit: int = Query(100, ge=1, le=200), db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    return _task_outputs(db.execute(_task_query(db, current, kb_id).order_by(ParseTask.id.desc()).limit(limit)).all())

def _document_chunks(db, doc):
    return db.scalars(select(Chunk).where(Chunk.document_id == doc.id, Chunk.kb_id == doc.kb_id,
        versions.current_chunk_filter(db, include_archived=True)).order_by(Chunk.seq_no)).all()

@router.get('/kbs/{kb_id}/docs/{doc_id}/chunks', response_model=list[ChunkOut])
def scoped_chunks(kb_id: int, doc_id: int, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    return _document_chunks(db, _load_document(db, kb_id, doc_id, current))

@router.get('/docs/{doc_id}/chunks', response_model=list[ChunkOut])
def list_chunks(doc_id: int, kb_id: int | None = Query(None, ge=1), db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, '文档不存在')
    return _document_chunks(db, _load_document(db, kb_id if kb_id is not None else doc.kb_id, doc_id, current))

@router.get('/kbs/{kb_id}/docs/{doc_id}/file', response_class=FileResponse)
def document_file(kb_id: int, doc_id: int, download: bool = Query(False), encoding: Literal['binary', 'base64'] = Query('binary'), db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    doc = _load_document(db, kb_id, doc_id, current)
    try:
        target = kb_service.document_file_path(doc)
        is_pdf = doc.file_type == 'pdf' and target.suffix.lower() == '.pdf' and Path(doc.filename).suffix.lower() == '.pdf'
        with target.open('rb') as source:
            prefix = source.read(5)
        is_pdf = is_pdf and prefix == b'%PDF-'
    except FileNotFoundError as error:
        raise HTTPException(404, '文档原文件不存在') from error
    except (OSError, ValueError) as error:
        raise HTTPException(409, '文档存储路径不可安全读取，请联系知识库所有者核对') from error
    if encoding == 'base64':
        # Some embedded browser PDF handlers consume binary XHR responses. The
        # client uses this same authorized endpoint only after an empty 200 body.
        if target.stat().st_size > kb_service.MAX_FILE_SIZE:
            raise HTTPException(413, '原文件超过浏览器预览大小限制')
        data = target.read_bytes()
        return JSONResponse({'content_base64': base64.b64encode(data).decode('ascii'),
                             'mime_type': 'application/pdf' if is_pdf else 'application/octet-stream',
                             'filename': doc.filename, 'size': len(data)},
                            headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff', 'Vary': 'Authorization'})
    return FileResponse(target, filename=doc.filename,
        media_type='application/pdf' if is_pdf and not download else 'application/octet-stream',
        content_disposition_type='inline' if is_pdf and not download else 'attachment',
        headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store', 'Vary': 'Authorization'})
