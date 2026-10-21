"""BM25 倒排检索：按库缓存分词结果，数据签名保证入库/重解析后更新。

缓存受知识片段总数与库数双重约束；超大库仍完整检索，不静默截断。
每次查询只核验数据库聚合签名，命中缓存后只遍历查询词的倒排项。
"""
from __future__ import annotations
import heapq
import math
import re
import threading
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
from sqlalchemy import func, select
from ..db.models import Chunk, Document
from ..db.session import get_session_factory
from .retrieval_filter import retrieval_allowed

_TOKEN_RE = re.compile(r'[\u4e00-\u9fffA-Za-z0-9]+')
_STOP = set('的 了 和 是 在 有 与 及 或 请 请问 我 我们 你 什么 哪些 如何 怎么 相关 可以 进行 这个 一个 为什么 吗 呢 啊 the a an is of to and in what how'.split())

def _segment(text: str) -> list[str]:
    try:
        import jieba
        tokens = jieba.lcut_for_search(text.lower())
    except ImportError:
        tokens = _TOKEN_RE.findall(text.lower())
    return [t for t in tokens if 0 < len(t) <= 40 and _TOKEN_RE.fullmatch(t) and t not in _STOP]

@dataclass(frozen=True)
class _Snapshot:
    signature: tuple
    meta: dict
    postings: dict
    lengths: dict
    average: float

class KeywordIndex:
    _cache = OrderedDict()
    _lock = threading.RLock()
    max_cached_kbs = 6
    max_cached_chunks = 25000

    def __init__(self) -> None:
        self._factory = get_session_factory()

    @classmethod
    def invalidate(cls, kb_id: int | None = None) -> None:
        with cls._lock:
            if kb_id is None:
                cls._cache.clear()
            else:
                for key in list(cls._cache):
                    if key[1] == kb_id:
                        del cls._cache[key]

    def _snapshot(self, kb_id: int, scope=None) -> _Snapshot:
        with self._factory() as db:
            key = (db.get_bind(), kb_id)
            from ..services import version_service as versions
            managed = versions.versioning_enabled(db)
            current_scope = scope if scope is not None else versions.retrieval_scope(db, [kb_id]) if managed else None
            valid = (Chunk.kb_id == kb_id, Document.kb_id == kb_id, versions.current_chunk_filter(db, scope=current_scope)) if managed else (Chunk.kb_id == kb_id, Document.kb_id == kb_id, Document.status == 'success')
            valid += (retrieval_allowed(Chunk.meta_json),)
            signature = tuple(db.execute(
                select(func.count(Chunk.id), func.max(Chunk.id), func.max(Chunk.created_at), func.max(Document.updated_at))
                .join(Document, Document.id == Chunk.document_id).where(*valid)
            ).one()) + ((current_scope['signature'],) if current_scope is not None else ('legacy',))
            with self._lock:
                cached = self._cache.get(key)
                if cached and cached.signature == signature:
                    self._cache.move_to_end(key)
                    return cached
                meta, postings, lengths = {}, defaultdict(dict), {}
                rows = db.execute(
                    select(Chunk.id, Chunk.document_id, Chunk.seq_no, Chunk.content, Chunk.page_no, Document.filename)
                    .join(Document, Document.id == Chunk.document_id).where(*valid)
                    .order_by(Chunk.id).execution_options(yield_per=1000)
                )
                for cid, did, seq, content, page, name in rows:
                    terms = Counter(_segment(content))
                    lengths[cid] = max(1, sum(terms.values()))
                    meta[cid] = {'chunk_id': cid, 'doc_id': did, 'doc_name': name, 'seq_no': seq, 'content': content, 'page_no': page or 0}
                    for term, count in terms.items():
                        postings[term][cid] = count
                snapshot = _Snapshot(signature, meta, dict(postings), lengths, sum(lengths.values()) / max(1, len(lengths)))
                if len(meta) <= self.max_cached_chunks:
                    self._cache[key] = snapshot
                    self._cache.move_to_end(key)
                    while len(self._cache) > self.max_cached_kbs or sum(len(v.meta) for v in self._cache.values()) > self.max_cached_chunks:
                        self._cache.popitem(last=False)
                else:
                    self._cache.pop(key, None)
                return snapshot

    def search(self, query: str, kb_id: int, top_k: int = 30, *, scope=None) -> list[dict]:
        terms = set(_segment(query))
        if not terms or top_k <= 0:
            return []
        index = self._snapshot(kb_id, scope=scope)
        n = len(index.meta)
        if not n:
            return []
        scores = defaultdict(float)
        for term in terms:
            posting = index.postings.get(term, {})
            if not posting:
                continue
            idf = math.log1p((n - len(posting) + .5) / (len(posting) + .5))
            for cid, tf in posting.items():
                denom = tf + 1.5 * (.25 + .75 * index.lengths[cid] / index.average)
                scores[cid] += idf * tf * 2.5 / denom
        ranked = heapq.nsmallest(top_k, scores, key=lambda cid: (-scores[cid], cid))
        return [dict(index.meta[cid], score=scores[cid]) for cid in ranked]
