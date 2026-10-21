"""授权范围内的运营统计：SQL 聚合，不逐库查询或加载全部事件。"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session
from ..api.deps import CurrentUser, require_operator
from ..db.models import Citation, Document, KnowledgeBase, Message, QaFeedback, StatEvent, User
from ..db.session import get_db
from ..schemas.stats import CitationStats, FeedbackStats, KbDist, ModeDist, OverviewOut, TrendPoint
from ..services.kb_service import get_visible_kb_ids
from ..services import version_service as versions
router = APIRouter(prefix='/api/stats', tags=['stats'])
CN = timezone(timedelta(hours=8))

def _ids(db, current):
    return get_visible_kb_ids(db, db.get(User, current.id))

@router.get('/overview', response_model=OverviewOut)
def overview(db: Session = Depends(get_db), current: CurrentUser = Depends(require_operator)):
    ids = _ids(db,current)
    modes = dict(db.execute(select(StatEvent.mode,func.count(StatEvent.id)).where(StatEvent.event_type=='qa_completed',StatEvent.kb_id.in_(ids)).group_by(StatEvent.mode)).all())
    qa_total = sum(modes.values())
    assistant_total = db.scalar(select(func.count(Message.id)).where(Message.role=='assistant',Message.kb_id.in_(ids))) or 0
    cited_messages, citation_total = db.execute(select(func.count(func.distinct(Citation.message_id)),func.count(Citation.id))
        .join(Message,Message.id==Citation.message_id).where(Message.role=='assistant',Message.kb_id.in_(ids))).one()
    doc_total, doc_success = db.execute(select(func.count(Document.id),func.sum(case((Document.status=='success',1),else_=0))).where(Document.kb_id.in_(ids), versions.document_listing_filter(db))).one()
    doc_success = doc_success or 0
    feedback_rows = dict(db.execute(select(QaFeedback.feedback_type,func.count(QaFeedback.id)).join(Message,Message.id==QaFeedback.message_id).where(Message.kb_id.in_(ids)).group_by(QaFeedback.feedback_type)).all())
    feedback = FeedbackStats(total=sum(feedback_rows.values()),**{key:feedback_rows.get(key,0) for key in ('like','dislike','no_relevant','lack_source')})
    distribution = db.execute(select(KnowledgeBase.id,KnowledgeBase.name,func.count(StatEvent.id))
        .outerjoin(StatEvent,and_(StatEvent.kb_id==KnowledgeBase.id,StatEvent.event_type=='qa_completed'))
        .where(KnowledgeBase.id.in_(ids)).group_by(KnowledgeBase.id,KnowledgeBase.name).order_by(KnowledgeBase.id)).all()
    return OverviewOut(qa_total=qa_total,assistant_total=assistant_total,cited_messages=cited_messages,
        cite_rate=round(cited_messages/assistant_total*100,1) if assistant_total else 0,
        doc_total=doc_total,doc_success=doc_success,doc_success_rate=round(doc_success/doc_total*100,1) if doc_total else 0,
        citation_total=citation_total,citation_stats=CitationStats(total=citation_total),feedback=feedback,
        mode_dist=ModeDist(local=modes.get('local',0),online=modes.get('online',0),degraded=modes.get('degraded',0),none=sum(v for k,v in modes.items() if k not in ('local','online','degraded'))),
        kb_dist=[KbDist(kb_id=kid,kb_name=name,qa_count=count) for kid,name,count in distribution])

@router.get('/trend', response_model=list[TrendPoint])
def trend(kb_id: int | None = None, db: Session = Depends(get_db), current: CurrentUser = Depends(require_operator)):
    ids = _ids(db,current)
    if kb_id is not None:
        if kb_id not in ids:
            raise HTTPException(403,'无权查看该知识库统计')
        ids = [kb_id]
    today = datetime.now(CN).date()
    start_date = today-timedelta(days=29)
    start_utc = datetime.combine(start_date,datetime.min.time(),tzinfo=CN).astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = datetime.combine(today+timedelta(days=1),datetime.min.time(),tzinfo=CN).astimezone(timezone.utc).replace(tzinfo=None)
    day = func.date(StatEvent.created_at,'+8 hours')
    rows = db.execute(select(day,StatEvent.mode,func.count(StatEvent.id)).where(StatEvent.event_type=='qa_completed',StatEvent.kb_id.in_(ids),StatEvent.created_at>=start_utc,StatEvent.created_at<end_utc).group_by(day,StatEvent.mode)).all()
    points = {(start_date+timedelta(days=i)).isoformat():{'date':(start_date+timedelta(days=i)).isoformat(),'qa_count':0,'local':0,'online':0,'degraded':0,'none':0,'cited':0} for i in range(30)}
    for date, mode, count in rows:
        if date in points:
            points[date]['qa_count'] += count
            points[date][mode if mode in ('local','online','degraded') else 'none'] += count
    # cited 统计当前保留且有引用的回答；累计问答事件不会随删除而消失。
    message_day = func.date(Message.created_at,'+8 hours')
    for date,count in db.execute(select(message_day,func.count(func.distinct(Message.id))).join(Citation,Citation.message_id==Message.id)
        .where(Message.role=='assistant',Message.kb_id.in_(ids),Message.created_at>=start_utc,Message.created_at<end_utc).group_by(message_day)):
        if date in points:
            points[date]['cited'] = count
    return [TrendPoint(**point) for point in points.values()]
