"""切片引擎：将解析出的结构化 Part 组装为可检索 Chunk 输入。

策略（对齐计划书 §14.1）：
- 普通段落按目标长度聚合，在段落边界成片；
- 表格/图片文字块整块成片（保持结构化）；
- 标题（heading）并入其后的首个内容片作为上下文线索；
- 超长段落先按句子边界、再按字符硬切。
"""
from __future__ import annotations

import re

from .doc_parser import Part

DEFAULT_MAX_CHARS = 700
DEFAULT_OVERLAP = 60


def _split_long_text(text: str, max_chars: int) -> list[str]:
    """对超长段落切片：句子边界优先，超长句再按字符硬切。"""
    pieces: list[str] = []
    for sentence in re.split(r"(?<=[。！？!?；;])\s*", text):
        if not sentence:
            continue
        if len(sentence) <= max_chars:
            pieces.append(sentence)
            continue
        for i in range(0, len(sentence), max_chars):
            pieces.append(sentence[i : i + max_chars])
    return pieces


def _join(current: list[str], piece: str, max_chars: int) -> bool:
    """尝试把 piece 追加到 current（返回是否放得下）。"""
    estimate = sum(len(p) + 1 for p in current)
    return (estimate + len(piece)) <= max_chars


def chunk_parts(parts: list[Part], max_chars: int = DEFAULT_MAX_CHARS) -> list[dict]:
    """返回 [{content, page_no, meta}]。标题作为其后首个内容片的前缀。"""
    chunks: list[dict] = []
    pending_heading: str = ""

    def emit(content: str, page_no: int, meta: dict) -> None:
        nonlocal pending_heading
        text = content.strip()
        if not text:
            return
        if pending_heading:
            text = f"{pending_heading}\n{text}"
            pending_heading = ""
        chunks.append({"content": text, "page_no": page_no, "meta": meta})

    buffer: list[str] = []
    buffer_page = 0
    buffer_meta: dict = {}

    def flush() -> None:
        nonlocal buffer, buffer_page, buffer_meta
        if buffer:
            emit("\n".join(buffer), buffer_page, buffer_meta)
        buffer = []
        buffer_page = 0
        buffer_meta = {}

    for part in parts:
        text = part.clean
        if not text:
            continue
        if part.kind == "heading":
            flush()
            pending_heading = "\n".join(filter(None, (pending_heading, text)))
            continue
        if part.kind in ("table", "image_text"):
            flush()
            emit(text, part.page_no, dict(part.meta))
            continue
        # 不跨 PDF 页合并，否则第 2 页正文会错误标注为第 1 页。
        if buffer and part.page_no != buffer_page:
            flush()
        # 普通段落聚合
        if text and not _join(buffer, text, max_chars):
            flush()
        if not buffer and pending_heading and len(text) > max_chars:
            # 标题无法并入超长段（标题单独承载），直接整段硬切
            heading = pending_heading
            pending_heading = ""
            for piece in _split_long_text(text, max_chars):
                chunks.append(
                    {"content": f"{heading}\n{piece}" if heading else piece,
                     "page_no": part.page_no, "meta": dict(part.meta)}
                )
            continue
        if len(text) > max_chars:
            # 超长段落：从 buffer 前缀处分片
            if buffer:
                buffer.append(text[: max_chars // 2])
                text = text[max_chars // 2 :]
                flush()
            pieces = _split_long_text(text, max_chars)
            if pieces:
                first, rest = pieces[0], pieces[1:]
                # 首片并入 pending_heading（如有）
                if pending_heading:
                    emit(first, part.page_no, dict(part.meta))
                else:
                    chunks.append(
                        {"content": first, "page_no": part.page_no, "meta": dict(part.meta)}
                    )
                for piece in rest:
                    chunks.append(
                        {"content": piece, "page_no": part.page_no, "meta": dict(part.meta)}
                    )
            continue
        buffer.append(text)
        buffer_page = buffer_page or part.page_no
        if not buffer_meta:
            buffer_meta = dict(part.meta)
    flush()
    if pending_heading:
        chunks.append({"content": pending_heading, "page_no": 0, "meta": {"heading_only": True}})
    return chunks
