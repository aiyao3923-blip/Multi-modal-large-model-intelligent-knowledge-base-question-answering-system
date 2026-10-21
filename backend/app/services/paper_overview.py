"""Explicit library-wide reading requests, not an empty-search relevance fallback.

Select bounded, page-citable representative evidence per document. This does not
summarize papers, call a model, lower topical relevance thresholds or read vectors.
The QA service rebinds every candidate and revalidates its source before egress.
"""
from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import func, select

from ..db.models import Chunk, Document
from ..engines.retrieval_filter import retrieval_allowed
from . import version_service as versions

MAX_OVERVIEW_DOCUMENTS = 64
MIN_SOURCE_CHARS = 160


def overview_intent(question: str) -> str | None:
    """Accept only unambiguously generic corpus questions, never arbitrary topics.

    Full matches deliberately leave dates, named papers, extra subjects, negation
    and singular-paper follow-ups on the normal relevance-gated search path.
    """
    text = re.sub(r'[\s，,。.!！?？；;：:、]', '', question).lower()
    text = re.sub(r'^(?:请帮我|请你|帮我|请)', '', text)
    corpus = r'(?:这些|所有|全部|(?:当前知识库|当前库|本库)(?:中|里|内)?的?)(?:论文|文献|文章|资料)'
    overview = rf'(?:概括|总结|概述|综述|梳理)(?:一下)?{corpus}(?:的)?(?:核心|主要)?(?:研究问题(?:与|和|及)贡献|研究目标(?:与|和|及)贡献|贡献|研究内容|内容|研究问题)?'
    if re.fullmatch(overview, text):
        return 'overview'
    if re.fullmatch(rf'(?:比较|对比|概括|总结)(?:一下)?(?:不同论文|{corpus})(?:的)?(?:研究)?方法(?:优势(?:和|与|及)局限|(?:与|和|及)(?:优势|局限))?', text):
        return 'methods'
    if re.fullmatch(rf'(?:解释|概括|总结|比较)(?:一下)?(?:论文中|这些论文中|{corpus})(?:的)?实验设计(?:与|和|及)评价指标', text):
        return 'experiments'
    if text in {'有哪些值得进一步研究的问题', '这些论文有哪些研究局限和未来方向',
                '总结这些论文的局限与未来研究方向', '总结这些论文的研究局限与未来方向'}:
        return 'gaps'
    english = re.sub(r'\s+', ' ', question.lower()).strip(' .?!')
    if re.fullmatch(r'(?:summari[sz]e|give an overview of) (?:these|all|the) papers(?: and their (?:research questions and contributions|main contributions))?', english):
        return 'overview'
    return None


_SECTION_PATTERNS = {
    'abstract': re.compile(r'(?:^|\n)\s*(?:abstract|摘要)\s*[:：.\-—–]?|\babstract\s*[:：\-—–]', re.I),
    'contribution': re.compile(r'\b(?:our|main|key) contributions?\b|\bwe (?:propose|present|introduce|develop)\b|主要贡献|本文(?:提出|贡献)', re.I),
    'method': re.compile(r'(?:^|\n)\s*(?:[\divx.]+\s*)?(?:method(?:ology)?|proposed (?:method|framework)|approach)\b|(?:^|\n)\s*(?:方法|研究方法)', re.I),
    'experiment': re.compile(r'(?:^|\n)\s*(?:[\divx.]+\s*)?(?:experiments?|evaluation|results)\b|\b(?:experimental (?:results|setup)|controlled experiments|evaluation (?:metrics|protocol))\b|实验(?:设计|设置|结果)|评价指标', re.I),
    'gap': re.compile(r'\b(?:limitations?|future (?:work|research)|open (?:questions|challenges))\b|研究局限|未来(?:工作|研究)|不足之处', re.I),
    'conclusion': re.compile(r'(?:^|\n)\s*(?:[\divx.]+\s*)?(?:conclusions?|discussion)\b|(?:^|\n)\s*(?:结论|讨论)', re.I),
    'introduction': re.compile(r'(?:^|\n)\s*(?:[\divx.]+\s*)?introduction\b|(?:^|\n)\s*(?:引言|研究背景)', re.I),
}
_PRIORITIES = {
    'overview': ('abstract', 'contribution', 'conclusion', 'introduction', 'method', 'experiment'),
    'methods': ('method', 'contribution', 'abstract', 'conclusion', 'experiment', 'introduction'),
    'experiments': ('experiment', 'method', 'contribution', 'abstract', 'conclusion'),
    'gaps': ('gap', 'conclusion', 'abstract', 'introduction', 'contribution'),
}


def _landmark(text, focus):
    priorities = _PRIORITIES.get(focus, _PRIORITIES['overview'])
    for index, name in enumerate(priorities):
        match = _SECTION_PATTERNS[name].search(text)
        if match:
            return len(priorities) - index, match.start()
    return 0, 0


def _body_text(text):
    stripped = text.strip()
    if len(stripped) < 80:
        return False
    excluded = re.search(r'acknowledg(?:e)?ments?\s*[:.]|funding\s*[:：]|致谢|^(?:references|bibliography)\s*$', stripped, re.I | re.M)
    return not excluded or excluded.start() >= len(stripped) * .5


def overview_candidates(db, kb_id, scope, focus):
    """Scan only eligible same-library/current-version text, with bounded memory."""
    valid = (
        Chunk.kb_id == kb_id, Document.kb_id == kb_id, Document.status == 'success',
        retrieval_allowed(Chunk.meta_json), versions.current_chunk_filter(db, scope=scope),
        func.length(func.trim(Chunk.content)) >= 80,
    )
    documents = (select(Chunk.document_id).join(Document, Document.id == Chunk.document_id)
                 .where(*valid).group_by(Chunk.document_id))
    total = int(db.scalar(select(func.count()).select_from(documents.subquery())) or 0)
    ids = list(db.scalars(documents.order_by(Chunk.document_id).limit(MAX_OVERVIEW_DOCUMENTS)))
    if not ids:
        return [], total
    rows = db.execute(select(Chunk.id, Chunk.document_id, Document.filename, Chunk.page_no,
                             Chunk.seq_no, Chunk.content).join(Document, Document.id == Chunk.document_id)
                      .where(*valid, Chunk.document_id.in_(ids)).order_by(Chunk.document_id, Chunk.seq_no, Chunk.id)
                      .execution_options(yield_per=200))
    best = defaultdict(list)
    for cid, did, filename, page, seq, text in rows:
        if not _body_text(text):
            continue
        weight, _ = _landmark(text, focus)
        hit = {'chunk_id': cid, 'doc_id': did, 'doc_name': filename, 'page_no': page or 0,
               'seq_no': seq or 0, 'content': text, 'score': 0.0}
        # Keep alternatives in case the best source becomes unreadable on rebind.
        best[did].append(((weight, -(seq or 0), -cid), hit))
        best[did].sort(key=lambda item: item[0], reverse=True)
        del best[did][3:]
    return [hit for did in ids for _, hit in best[did]], total


def _excerpt(text, quota, focus):
    text = text.strip()
    _, start = _landmark(text, focus)
    text = text[start:].strip()
    if len(text) <= quota:
        return text
    suffix = '\n[片段节选]'
    return text[:max(0, quota - len(suffix))].rstrip() + suffix


def select_overview_sources(candidates, budget, focus):
    """One representative per paper with fair context budget and stable ordering."""
    unique, seen = [], set()
    for source in candidates:
        if source['doc_id'] in seen or not source['content'].strip():
            continue
        seen.add(source['doc_id'])
        unique.append(source)
    picked, minimum_cost = [], 0
    for source in unique[:MAX_OVERVIEW_DOCUMENTS]:
        cost = len(source['doc_name']) + 60 + min(MIN_SOURCE_CHARS, len(source['content'].strip()))
        if minimum_cost + cost > budget:
            break
        picked.append(source)
        minimum_cost += cost
    remaining = budget - sum(len(source['doc_name']) + 60 for source in picked)
    result = []
    for index, source in enumerate(picked):
        quota = min(5000, remaining // (len(picked) - index))
        excerpt = _excerpt(source['content'], quota, focus)
        result.append(dict(source, content=excerpt))
        remaining -= len(excerpt)
    return result


def overview_notice(trace):
    covered, total = trace.get('covered_documents', 0), trace.get('total_documents', 0)
    if not total:
        return ''
    limited = '，受上下文预算或正文可用性限制，本次为部分覆盖' if covered < total else ''
    return f'资料总览：本次覆盖 {covered}/{total} 篇可检索资料的代表性片段{limited}；并非全文通读。未提供的实验细节和结论不能补猜。'