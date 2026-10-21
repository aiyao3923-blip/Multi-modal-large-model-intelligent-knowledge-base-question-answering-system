"""PDF 解析：pypdf 抽取文本，按页分块；每页文本较长时保留页信息，交由切片器进一步切分。"""
from __future__ import annotations

from pathlib import Path

from . import ParseError, Part


class PdfParser:
    def parse(self, path: Path) -> list[Part]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ParseError("缺少 pypdf 依赖") from exc
        try:
            reader = PdfReader(str(path))
            parts: list[Part] = []
            for idx, page in enumerate(reader.pages, start=1):
                text = (page.extract_text() or "").strip()
                if text:
                    parts.append(Part(text=text, page_no=idx, kind="paragraph"))
                # 扫描型 PDF 无文本层 → 留给 OCR 通道（image_text）
            return parts
        except ParseError:
            raise
        except Exception as exc:
            raise ParseError(f"PDF 解析失败: {exc}") from exc
