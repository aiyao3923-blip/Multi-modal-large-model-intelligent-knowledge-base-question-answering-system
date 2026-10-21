"""问答、会话与反馈；写入和查看均检查账号所有权与知识库授权。"""
from __future__ import annotations
from datetime import datetime, timezone
from contextlib import nullcontext
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from ..api.deps import CurrentUser, get_current_user, require_operator
from ..db.models import ChatSession, Citation, Document, KnowledgeBase, Message, QaFeedback, StatEvent, User
from ..db.session import get_db
from ..schemas.common import MsgOut, PageOut
from ..schemas.qa import CitationOut, FeedbackCreate, FeedbackOut, MessageOut, QaReq, QaResp, SessionCreate, SessionOut
from ..services import kb_service, version_service as versions
from ..services.qa_service import GENERATION_NOTICES, _select_channel, failure_history_view, persist_qa_result, run_qa
from ..engines.embedder import get_embedding_identity
router = APIRouter(prefix='/api/qa', tags=['qa'])
DEGRADED_NOTICE = '仅检索证据，尚未生成回答。'

def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def _visible(db, current):
    return kb_service.get_visible_kb_ids(db, db.get(User, current.id))

def _check_kb_visible(db, kb_id, current, enabled=False):
    kb = db.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(404, '知识库不存在')
    if kb.id not in _visible(db, current):
        raise HTTPException(403, '无权访问该知识库')
    if enabled and kb.status != 'enabled':
        raise HTTPException(409, '知识库已停用，请选择其他知识库')
    return kb

def _owned_session(db, session_id, current):
    session = db.get(ChatSession, session_id)
    if not session or session.user_id != current.id:
        raise HTTPException(404, '会话不存在')
    return session

@router.get('/capabilities')
def capabilities(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)):
    channel = _select_channel(db)
    mode = channel.channel_type if channel else 'degraded'
    notice = GENERATION_NOTICES['unconfigured'] if not channel else '生成通道已配置，实际结果以每条回答为准。'
    try:
        identity = get_embedding_identity()
        retrieval = 'BM25 + 本地多语言向量混合检索（E5 384维，CPU）' if identity else '关键词检索；显式配置本地 Embedding 后可增强为混合检索'
    except (OSError, ValueError, ImportError):
        retrieval = '本地 Embedding 配置需检查；不能保证向量检索可用'
    version_state = versions.capabilities(db)
    return {'mode': mode, 'notice': notice, 'retrieval': retrieval, 'versioning_enabled': version_state['enabled'], 'versioning_state': version_state['mode'], 'versioning_schema': version_state['schema_version'], 'generation_configured': bool(channel), 'model_name': channel.model_name if channel else ''}

@router.get('/sessions', response_model=list[SessionOut])
def list_sessions(q: str = Query('', max_length=100), kb_id: int | None = Query(None), db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    query = select(ChatSession).where(ChatSession.user_id == current.id, ChatSession.kb_id.in_(_visible(db, current)))
    if kb_id is not None:
        _check_kb_visible(db, kb_id, current)
        query = query.where(ChatSession.kb_id == kb_id)
    if q.strip():
        query = query.where(ChatSession.title.contains(q.strip(), autoescape=True))
    return db.scalars(query.order_by(ChatSession.updated_at.desc(), ChatSession.id.desc()).limit(200)).all()

@router.post('/sessions', response_model=SessionOut)
def create_session(data: SessionCreate, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    _check_kb_visible(db, data.kb_id, current, enabled=True)
    session = ChatSession(user_id=current.id, kb_id=data.kb_id, title=data.title)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session

@router.patch('/sessions/{session_id}', response_model=SessionOut)
def rename_session(session_id: int, title: str = Query(..., min_length=1, max_length=100), db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    session = _owned_session(db, session_id, current)
    _check_kb_visible(db, session.kb_id, current)
    if not title.strip():
        raise HTTPException(422, '会话名称不能为空')
    session.title = title.strip()
    session.updated_at = _now()
    db.commit()
    db.refresh(session)
    return session

@router.delete('/sessions/{session_id}', response_model=MsgOut)
def delete_session(session_id: int, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    session = _owned_session(db, session_id, current)
    ids = select(Message.id).where(Message.session_id == session_id)
    db.query(QaFeedback).filter(QaFeedback.message_id.in_(ids)).delete(synchronize_session=False)
    db.query(Citation).filter(Citation.message_id.in_(ids)).delete(synchronize_session=False)
    db.query(Message).filter(Message.session_id == session_id).delete(synchronize_session=False)
    # 累计统计不因删会话被改写，但不保留指向已删除会话的关联。
    db.query(StatEvent).filter(StatEvent.session_id == session_id).update({StatEvent.session_id:None}, synchronize_session=False)
    db.delete(session)
    db.commit()
    return MsgOut(message='会话、引用与反馈记录已删除')

@router.post('/ask', response_model=QaResp)
def ask(req: QaReq, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    return perform_ask(req, db, current)


def perform_ask(req: QaReq, db: Session, current: CurrentUser, *, control=None):
    if control is not None:
        control.checkpoint()
    kb = _check_kb_visible(db, req.kb_id, current, enabled=True)
    new_session = req.create_new_session or not req.session_id
    if new_session:
        session = ChatSession(user_id=current.id, kb_id=req.kb_id, title=req.question[:30])
        db.add(session)
        db.commit()
        db.refresh(session)
    else:
        session = _owned_session(db, req.session_id, current)
        if session.kb_id != req.kb_id:
            raise HTTPException(409, '一个会话仅对应一个知识库，请新建对话后切换')
    history = list(reversed(db.scalars(select(Message).where(Message.session_id == session.id).order_by(Message.id.desc()).limit(20)).all()))
    try:
        kwargs = {'control': control} if control is not None else {}
        result = run_qa(db, current.id, session.id, req.kb_id, req.question, history, **kwargs)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # 等待模型期间会话可能被另一个客户端删除，不产生孤立消息。
    if not db.scalar(select(ChatSession.id).where(ChatSession.id == session.id)):
        raise HTTPException(409, '会话已被删除，未保存本次回答')
    _check_kb_visible(db, req.kb_id, current, enabled=True)
    with control.finalizing() if control is not None else nullcontext():
        assistant = persist_qa_result(db, user_id=current.id, session_id=session.id, kb_id=req.kb_id, question=req.question, result=result)
    return QaResp(message_id=assistant.id, session_id=session.id, answer=result['answer'], mode=result['mode'], kb_name=kb.name,
                  citations=result['citations'], latency_ms=result['latency_ms'], degraded=result['mode']=='degraded', notice=result.get('notice', ''), generation_status=result.get('generation_status', ''), retrieval=result.get('retrieval'))

@router.get('/sessions/{session_id}/messages', response_model=list[MessageOut])
def session_messages(session_id: int, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    session = _owned_session(db, session_id, current)
    _check_kb_visible(db, session.kb_id, current)
    messages = db.scalars(select(Message).where(Message.session_id == session_id).order_by(Message.id)).all()
    if any(message.kb_id != session.kb_id for message in messages):
        raise HTTPException(409, '会话的知识库范围不一致，无法展示混合记录')
    ids = [message.id for message in messages]
    citations = {}
    citation_rows = db.scalars(select(Citation).where(Citation.message_id.in_(ids)).order_by(Citation.rank)).all()
    doc_ids = {c.document_id for c in citation_rows}
    doc_scopes = dict(db.execute(select(Document.id, Document.kb_id).where(Document.id.in_(doc_ids))).all()) if doc_ids else {}
    if any(c.kb_id != session.kb_id or (c.document_id in doc_scopes and doc_scopes[c.document_id] != session.kb_id) for c in citation_rows):
        raise HTTPException(409, '会话的引用范围不一致，无法展示混合记录')
    for citation in citation_rows:
        citations.setdefault(citation.message_id, []).append(CitationOut.model_validate(citation))
    feedbacks = dict(db.execute(select(QaFeedback.message_id, QaFeedback.feedback_type).where(QaFeedback.message_id.in_(ids), QaFeedback.user_id == current.id)).all())
    result = []
    for message in messages:
        out = MessageOut.model_validate(message)
        out.citations, out.feedback_type = citations.get(message.id, []), feedbacks.get(message.id, '')
        if message.role == 'assistant':
            out.generation_status = message.status if message.status in GENERATION_NOTICES else ('generated' if message.mode in ('local', 'online') else 'no_evidence' if message.mode == 'none' else 'unavailable')
            out.notice = GENERATION_NOTICES.get(out.generation_status, '')
            if out.generation_status in {'unavailable', 'unconfigured', 'invalid_citations'}:
                out.content, out.notice = failure_history_view(message.content, out.generation_status)
        result.append(out)
    return result

@router.post('/feedback', response_model=FeedbackOut)
def create_feedback(data: FeedbackCreate, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)):
    message = db.get(Message, data.message_id)
    if not message or message.role != 'assistant':
        raise HTTPException(404, '助手消息不存在')
    _owned_session(db, message.session_id, current)
    _check_kb_visible(db, message.kb_id, current)
    feedback = db.scalar(select(QaFeedback).where(QaFeedback.message_id == data.message_id, QaFeedback.user_id == current.id))
    if not feedback:
        feedback = QaFeedback(message_id=data.message_id, user_id=current.id)
        db.add(feedback)
    feedback.feedback_type, feedback.comment, feedback.status = data.feedback_type, data.comment.strip(), 'pending'
    feedback.handler_id, feedback.handled_at, feedback.handler_note = None, None, ''
    db.commit()
    db.refresh(feedback)
    return feedback

def _feedback_query(db, current, status=None, q=''):
    query = select(QaFeedback, User.username, Message.question, Message.content, Message.kb_id).join(Message, Message.id == QaFeedback.message_id).join(User, User.id == QaFeedback.user_id).where(Message.kb_id.in_(_visible(db, current)))
    if status:
        query = query.where(QaFeedback.status == status)
    if q.strip():
        term = q.strip()
        query = query.where(or_(Message.question.contains(term, autoescape=True), QaFeedback.comment.contains(term, autoescape=True), User.username.contains(term, autoescape=True)))
    return query

def _feedback_outputs(rows):
    items = []
    for feedback, username, question, answer, kb_id in rows:
        out = FeedbackOut.model_validate(feedback)
        out.username, out.question, out.answer, out.kb_id = username, question, answer, kb_id
        items.append(out)
    return items

@router.get('/feedbacks/page', response_model=PageOut[FeedbackOut])
def feedbacks_page(status: Literal['pending','handled','closed'] | None = None, q: str = Query('', max_length=200), page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), current: CurrentUser = Depends(require_operator)):
    query = _feedback_query(db, current, status, q)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(query.order_by(QaFeedback.id.desc()).offset((page-1)*size).limit(size)).all()
    return PageOut(items=_feedback_outputs(rows), total=total, page=page, size=size)

@router.get('/feedbacks', response_model=list[FeedbackOut])
def list_feedbacks(status: Literal['pending','handled','closed'] | None = None, db: Session = Depends(get_db), current: CurrentUser = Depends(require_operator)):
    return _feedback_outputs(db.execute(_feedback_query(db, current, status).order_by(QaFeedback.id.desc()).limit(200)).all())

@router.post('/feedbacks/{feedback_id}/handle', response_model=FeedbackOut)
def handle_feedback(feedback_id: int, action: Literal['pending','handled','closed'], note: str = Query('', max_length=1000), db: Session = Depends(get_db), current: CurrentUser = Depends(require_operator)):
    feedback = db.get(QaFeedback, feedback_id)
    if not feedback:
        raise HTTPException(404, '反馈不存在')
    message = db.get(Message, feedback.message_id)
    if not message or message.kb_id not in _visible(db, current):
        raise HTTPException(403, '无权处理该反馈')
    feedback.status = action
    feedback.handler_id = None if action == 'pending' else current.id
    feedback.handled_at = None if action == 'pending' else _now()
    if note.strip():
        feedback.handler_note = note.strip()
    db.commit()
    db.refresh(feedback)
    return feedback
