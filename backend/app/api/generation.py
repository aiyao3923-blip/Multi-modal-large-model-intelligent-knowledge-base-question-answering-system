"""Authenticated progress streams: complete answers only, no reasoning/token leaks."""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .deps import CurrentUser, get_current_user
from .qa import _check_kb_visible, _owned_session, perform_ask
from ..db.session import get_db, get_session_factory
from ..schemas.qa import QaReq, TranslationReq
from ..services.generation_runtime import GenerationBusy, GenerationCancelled, GenerationControl, slots
from ..services.translation_service import run_translation, translation_source
from ..services.version_service import VersionError

router = APIRouter(prefix='/api/qa', tags=['qa'])
HEARTBEAT_SECONDS = 5.0


def _offer(events, event):
    # Progress is replaceable and contains no prompt/model text. Never buffer an
    # unbounded per-token event list while a slow browser reads the response.
    try:
        events.put_nowait(event)
    except queue.Full:
        try:
            events.get_nowait()
        except queue.Empty:
            pass
        events.put_nowait(event)


def generation_response(request, owner_id, work):
    try:
        release = slots.acquire(owner_id)
    except GenerationBusy as error:
        raise HTTPException(429, str(error)) from None
    events = queue.Queue(maxsize=1)
    control = GenerationControl(lambda event: _offer(events, event))

    def worker():
        try:
            control.checkpoint()
            result = work(control)
            control.checkpoint()
            _offer(events, {'event': 'result', 'data': result})
        except GenerationCancelled:
            _offer(events, {'event': 'error', 'status': 499, 'detail': '已停止生成。若服务刚完成，请先刷新历史记录，避免重复发送。'})
        except (HTTPException, VersionError) as error:
            _offer(events, {'event': 'error', 'status': error.status_code, 'detail': str(error.detail)})
        except Exception:
            # Exception strings can contain a model request, documents or keys.
            _offer(events, {'event': 'error', 'status': 500, 'detail': '处理未完成，请检查服务状态后重试；未返回不完整答案。'})
        finally:
            release()

    async def stream():
        last_sent = time.monotonic()
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = events.get_nowait()
                except queue.Empty:
                    if time.monotonic() - last_sent >= HEARTBEAT_SECONDS:
                        event = control.heartbeat()
                    else:
                        await asyncio.sleep(.1)
                        continue
                yield json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n'
                last_sent = time.monotonic()
                if event['event'] in {'result', 'error'}:
                    return
        finally:
            control.cancel()

    thread = threading.Thread(target=worker, name='paper-generation', daemon=True)
    try:
        thread.start()
    except Exception:
        release()
        raise HTTPException(503, '生成任务暂时无法启动，请稍后再试') from None
    return StreamingResponse(stream(), media_type='application/x-ndjson', headers={
        'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no', 'X-Content-Type-Options': 'nosniff',
    })


@router.post('/ask/stream')
def ask_stream(req: QaReq, request: Request, db: Session = Depends(get_db),
               current: CurrentUser = Depends(get_current_user)):
    _check_kb_visible(db, req.kb_id, current, enabled=True)
    if req.session_id and not req.create_new_session:
        session = _owned_session(db, req.session_id, current)
        if session.kb_id != req.kb_id:
            raise HTTPException(409, '一个会话仅对应一个知识库，请新建对话后切换')
    factory = get_session_factory()

    def work(control):
        with factory() as worker_db:
            return perform_ask(req, worker_db, current, control=control).model_dump(mode='json')
    return generation_response(request, current.id, work)


@router.post('/translate/stream')
def translate_stream(req: TranslationReq, request: Request, db: Session = Depends(get_db),
                     current: CurrentUser = Depends(get_current_user)):
    # Check ownership before starting any task. The worker repeats this against
    # its own DB session before every request and before releasing a translation.
    translation_source(db, current.id, req)
    factory = get_session_factory()

    def work(control):
        with factory() as worker_db:
            return run_translation(worker_db, current.id, req, control=control).model_dump(mode='json')
    return generation_response(request, current.id, work)