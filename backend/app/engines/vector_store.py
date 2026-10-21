"""完整向量检索：有界进程内矩阵 LRU，保留调用方的 KB 授权与版本 scope。

热查询只读取文档级轻量签名，不读取正文/JSON 向量；SQLite 范围、签名和
向量共享真实读事务。最多缓存 6 个库快照、128 MiB 矩阵（不含引用文本）；
超限库逐条完整检索，不截断。缓存不跨进程，导入器应在提交后 invalidate。
签名依赖正常业务写入的 Document.updated_at、片段数量/ID/创建时间及 scope。
不保证发现绕过业务 API、保留这些元数据的手工向量/正文修改；此时必须显式
invalidate（每个进程）或重启。模型身份非空时，不接受无身份或其他模型向量。
"""
from __future__ import annotations
import heapq
import json
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np
from sqlalchemy import func, select
from ..db.models import Chunk, Document
from ..db.session import get_session_factory
from .retrieval_filter import retrieval_allowed

def l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm > 1e-9 else vec

def cosine(a: list[float], b: list[float]) -> float:
    if not a or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))

def encode_vector_json(vec: list[float]) -> str:
    return json.dumps([round(float(v), 6) for v in vec])

def decode_vector_json(value: str) -> list[float]:
    return json.loads(value)

def _embedding_identity():
    # Lazy import: both ingestion paths import our public encoding helpers.
    from . import embedder
    getter = getattr(embedder, 'get_embedding_identity', None)
    return getter() if getter is not None else None

@dataclass(frozen=True)
class _Snapshot:
    signature: tuple
    matrix: np.ndarray
    meta: tuple

def _vectors(rows, dimension, identity):
    for cid, did, name, page, content, raw, metadata in rows:
        try:
            if identity:
                meta = json.loads(metadata or '{}')
                if not isinstance(meta, dict) or meta.get('embedding_model_id') != identity:
                    continue
            vector = decode_vector_json(raw)
            if not isinstance(vector, list) or len(vector) != dimension or not all(
                isinstance(v, (int, float)) and math.isfinite(v) for v in vector
            ):
                continue
        except (ValueError, TypeError, OverflowError):
            continue  # Corrupt row only; database/getter/programming errors propagate.
        yield dict(chunk_id=cid, doc_id=did, doc_name=name, page_no=page or 0, content=content), vector

def _rank(scored, top_k, threshold):
    candidates = (dict(meta, score=float(score)) for meta, score in scored
                  if math.isfinite(score) and (threshold is None or score >= threshold))
    return heapq.nlargest(top_k, candidates, key=lambda item: item['score'])

class VectorStore:
    _cache = OrderedDict()
    _lock = threading.RLock()
    max_cached_kbs = 6
    max_cache_bytes = 128 * 1024 * 1024

    @classmethod
    def invalidate(cls, kb_id: int | None = None) -> None:
        with cls._lock:
            for key in list(cls._cache):
                if kb_id is None or key[1] == kb_id:
                    del cls._cache[key]

    def search(self, query_vec, kb_id: int, top_k: int = 20, threshold: float | None = None, *, scope=None) -> list[dict]:
        if query_vec is None or top_k <= 0:
            return []
        try:
            query = np.asarray(query_vec, dtype=np.float64)
            if query.ndim != 1 or not query.size or not np.isfinite(query).all():
                return []
            query = l2_normalize(query.tolist())
        except (ValueError, TypeError, OverflowError):
            return []
        identity = _embedding_identity() or None
        # Serialize snapshot acquisition with invalidation; never wait on this lock
        # while holding a SQLite read transaction. Dot products run outside it.
        with self._lock, get_session_factory()() as db:
            from ..services import version_service as versions
            key = (db.get_bind(), kb_id)
            managed = versions.versioning_enabled(db)
            connection = db.connection()
            if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
                # sqlite3 legacy transaction control does NOT BEGIN for SELECT.
                connection.exec_driver_sql('BEGIN')
            current_scope = scope if scope is not None else versions.retrieval_scope(db, [kb_id]) if managed else None
            version_filter = versions.current_chunk_filter(db, scope=current_scope) if managed else Document.status == 'success'
            valid = (Chunk.kb_id == kb_id, Document.kb_id == kb_id, Chunk.vector_json.isnot(None), version_filter)
            valid += (retrieval_allowed(Chunk.meta_json),)
            stats = tuple(db.execute(select(
                Document.id, Document.updated_at, Document.filename, Document.status,
                func.count(Chunk.id), func.max(Chunk.id), func.max(Chunk.created_at)
            ).select_from(Chunk).join(Document, Document.id == Chunk.document_id)
                .where(*valid).group_by(Document.id).order_by(Document.id)).all())
            # Per-document timestamps avoid MAX(updated_at) masking an older doc's
            # edit. Include actual scope contents, not just a caller-supplied hash.
            signature = (stats, managed, json.dumps(current_scope, sort_keys=True), identity, len(query))
            snapshot = self._cache.get(key)
            if snapshot is None or snapshot.signature != signature:
                self._cache.pop(key, None)
                count = sum(row[4] for row in stats)
                rows = db.execute(select(
                    Chunk.id, Chunk.document_id, Document.filename, Chunk.page_no,
                    Chunk.content, Chunk.vector_json, Chunk.meta_json
                ).select_from(Chunk).join(Document, Document.id == Chunk.document_id)
                    .where(*valid).order_by(Chunk.id).execution_options(yield_per=500))
                vectors = _vectors(rows, len(query), identity)
                if self.max_cached_kbs <= 0 or count * len(query) * 8 > self.max_cache_bytes:
                    return _rank(((meta, cosine(query, vector)) for meta, vector in vectors), top_k, threshold)
                matrix = np.empty((count, len(query)), dtype=np.float64)
                meta = []
                for item, vector in vectors:
                    matrix[len(meta)] = vector
                    meta.append(item)
                matrix.resize((len(meta), len(query)), refcheck=False)
                matrix.setflags(write=False)
                snapshot = _Snapshot(signature, matrix, tuple(meta))
                self._cache[key] = snapshot
                while (len(self._cache) > self.max_cached_kbs or
                       sum(item.matrix.nbytes for item in self._cache.values()) > self.max_cache_bytes):
                    self._cache.popitem(last=False)
            self._cache.move_to_end(key)
        with np.errstate(over='ignore', invalid='ignore'):
            scores = snapshot.matrix @ query
        return _rank(zip(snapshot.meta, scores), top_k, threshold)

vector_store = VectorStore()
