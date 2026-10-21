"""Opt-in academic retrieval labels; never delete/rewrite source text or vectors.

Bibliographies remain available in document/chunk viewers, but are not evidence
for a paper's own method/results in default QA. Uncertain locations are retained.
"""
from __future__ import annotations
import re

PROFILE = 'academic-v1'
REF = re.compile(r'(?im)^[ \t]*(?:(?:[IVXLC]+|\d+)[.)]?[ \t]+)?(?:references|bibliography)[ \t]*$')
APPENDIX = re.compile(r'(?im)^[ \t]*(?:(?:[A-Z]|[IVXLC]+|\d+)[.)]?[ \t]+)?(?:appendi(?:x|ces)(?:[ \t]+[A-Z0-9]+)?(?:[.: /\t].*)?|supplement(?:ary|al)[ \t]+(?:material|materials|information)(?:[.: \t].*)?)[ \t]*$')
CHECKLIST = re.compile(r'(?im)^[ \t]*(?:(?:NeurIPS|ICML|ICLR)[ \t]+)?(?:paper|submission)[ \t]+checklist[ \t]*$')
EMAIL = re.compile(r'(?:e-?mail\s*:\s*)?[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}[;,.:]?$', re.I)
URL = re.compile(r'(?:https?://|www\.)\S+[;,.:]?$', re.I)


def normalized(text):
    return re.sub(r'\s+', ' ', text).strip()


def profile_enabled(kb_id):
    from ..core.config import settings
    ids = settings.get('retrieval', 'academic_kb_ids', default=[])
    if not isinstance(ids, list) or any(type(v) is not int or v < 1 for v in ids):
        raise ValueError('retrieval.academic_kb_ids must be positive integer IDs')
    return kb_id in ids


def low_information(text):
    text = normalized(text)
    if not text:
        return 'empty'
    if EMAIL.fullmatch(text) or URL.fullmatch(text):
        return 'locator_only'
    han = len(re.findall(r'[\u3400-\u9fff]', text))
    letters = len(re.findall(r'[A-Za-z]', text))
    words = re.findall(r"[A-Za-z][A-Za-z'-]+", text)
    # Keep concise Chinese statements, e.g. '许可仍待补证'. Short bare
    # symbols/numbers/labels have too little context to support a QA claim.
    if han >= 6:
        return None
    if letters + han < 12 or (len(text) < 40 and len(words) < 4):
        return 'low_information'
    return None


def reference_regions(parts):
    pages = {}
    for part in parts:
        if part.page_no > 0:
            pages.setdefault(part.page_no, []).append(part.clean)
    texts, regions, state = {}, {}, None
    for number in sorted(pages):
        raw = '\n'.join(pages[number])
        page = normalized(raw)
        texts[number] = page
        events = [(m.start(), 'bibliography') for m in REF.finditer(raw)]
        events += [(m.start(), 'reporting_checklist') for m in CHECKLIST.finditer(raw)]
        events += [(m.start(), None) for m in APPENDIX.finditer(raw) if len(m.group().strip()) < 160]
        spans, cursor = [], 0
        for offset, is_reference in sorted(events):
            position = len(normalized(raw[:offset]))
            if state and position > cursor:
                spans.append((cursor, position, state))
            cursor, state = position, is_reference
        if state and len(page) > cursor:
            spans.append((cursor, len(page), state))
        regions[number] = spans
    return texts, regions


def annotate_chunks(parts, chunks, file_type):
    """Return new metadata dictionaries; chunk boundaries/content stay identical."""
    pages, references = reference_regions(parts) if file_type == 'pdf' else ({}, {})
    cursors, result = {}, []
    for chunk in chunks:
        original = chunk.get('meta', {})
        if not isinstance(original, dict):
            raise ValueError('Chunk metadata must be an object')
        meta = dict(original)
        text = chunk['content']
        reason = low_information(text)
        section = 'body'
        page_no = chunk.get('page_no', 0) or 0
        if file_type == 'pdf' and page_no in pages:
            page, spans, piece = pages[page_no], references[page_no], normalized(text)
            start = page.find(piece, cursors.get(page_no, 0)) if piece else -1
            if start < 0 and piece:
                start = page.find(piece)
            if start >= 0:
                end = start + len(piece)
                cursors[page_no] = end
                for label in ('bibliography', 'reporting_checklist'):
                    overlap = sum(max(0, min(end, right) - max(start, left)) for left, right, kind in spans if kind == label)
                    if piece and overlap / len(piece) >= .5:
                        section, reason = label, label
                        break
            elif len(spans) == 1 and spans[0][:2] == (0, len(page)) and page:
                section = reason = spans[0][2]
        if not reason and text.lstrip().startswith('> ID:') and 'source SHA-256' in text:
            reason, section = 'source_metadata', 'provenance'
        meta.update(retrieval_profile=PROFILE, retrieval_excluded=bool(reason),
                    retrieval_reason=reason or 'body', retrieval_section=section)
        result.append(meta)
    return result


def annotate_for_kb(kb_id, parts, chunks, file_type):
    if profile_enabled(kb_id):
        for chunk, meta in zip(chunks, annotate_chunks(parts, chunks, file_type)):
            chunk['meta'] = meta
    return chunks
