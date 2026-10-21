"""Bounded concurrent generation and cancellation; never stores prompts or answers."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager


class GenerationCancelled(RuntimeError):
    """An explicit client disconnect/stop, not a provider failure or retry signal."""


class GenerationBusy(RuntimeError):
    """Concurrency is bounded independently of input/output text length."""


class GenerationControl:
    def __init__(self, emit=lambda event: None):
        self._cancelled = threading.Event()
        self._lock = threading.RLock()
        self._abort = None
        self._emit = emit
        self._started = time.monotonic()
        self._stage = 'retrieving'
        self._received_chars = 0

    def checkpoint(self):
        if self._cancelled.is_set():
            raise GenerationCancelled('已停止生成')

    def set_abort(self, callback):
        with self._lock:
            self._abort = callback
            cancelled = self._cancelled.is_set()
        if cancelled:
            self._close(callback)
            self.checkpoint()

    def clear_abort(self, callback):
        with self._lock:
            if self._abort is callback:
                self._abort = None

    @staticmethod
    def _close(callback):
        if callback is not None:
            try:
                callback()
            except Exception:
                # Closing an already-closed transport must not initiate a retry.
                pass

    def cancel(self):
        with self._lock:
            if self._cancelled.is_set():
                return
            self._cancelled.set()
            callback = self._abort
            self._abort = None
        self._close(callback)

    def progress(self, stage, **details):
        self.checkpoint()
        if stage not in {'retrieving', 'generating', 'validating', 'translating'}:
            return
        with self._lock:
            if stage != self._stage:
                self._received_chars = 0
            self._stage = stage
            count = details.get('received_chars')
            if type(count) is int and count >= 0:
                self._received_chars = count
            event = {'event': 'status', 'stage': stage, 'received_chars': self._received_chars}
        self._emit(event)

    def heartbeat(self):
        with self._lock:
            return {'event': 'heartbeat', 'stage': self._stage,
                    'received_chars': self._received_chars,
                    'elapsed_seconds': max(0, int(time.monotonic() - self._started))}

    @contextmanager
    def finalizing(self):
        # Serialize the last checkpoint and persistence against cancellation. If
        # cancellation arrives after commit starts, a complete result may exist;
        # clients must refresh history rather than automatically resend.
        with self._lock:
            self.checkpoint()
            yield


class GenerationSlots:
    def __init__(self, maximum=4):
        self.maximum = maximum
        self._lock = threading.Lock()
        self._owners = set()

    def acquire(self, owner_id):
        with self._lock:
            if owner_id in self._owners or len(self._owners) >= self.maximum:
                raise GenerationBusy('已有生成任务或服务繁忙，请停止当前任务或稍后再试')
            self._owners.add(owner_id)
        released = False

        def release():
            nonlocal released
            with self._lock:
                if not released:
                    self._owners.discard(owner_id)
                    released = True
        return release


slots = GenerationSlots()