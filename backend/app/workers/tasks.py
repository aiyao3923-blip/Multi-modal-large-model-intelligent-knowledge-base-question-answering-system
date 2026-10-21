"""固定两个后台工作线程，避免每份上传文件创建一个线程。"""
from __future__ import annotations
import threading
from concurrent.futures import ThreadPoolExecutor
from ..engines.doc_ingest import ingest_document

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='knowledge-parse')
_lock = threading.Lock()
_pending = set()

def submit_parse(task_id: int) -> None:
    with _lock:
        if task_id in _pending:
            return
        _pending.add(task_id)
    def run():
        try:
            return ingest_document(task_id)
        finally:
            with _lock:
                _pending.discard(task_id)
    try:
        _executor.submit(run)
    except RuntimeError:
        with _lock:
            _pending.discard(task_id)
        raise

def run_sync_parse(task_id: int) -> dict:
    return ingest_document(task_id)
