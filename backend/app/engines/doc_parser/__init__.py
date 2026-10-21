"""文档解析引擎入口：按扩展名路由到具体解析器，输出统一「结构化片段」。

设计（对齐计划书 §14.1 入库链与 D4 多模态边界）：
- 输出元素统一为 Part(text, page_no, kind)，kind ∈ paragraph/heading/table/image_text；
- 表格被转换为可检索的“结构化文字块”，图片走 OCR（可选，缺则标记未解析）；
- 解析器仅依赖 myenv12 已装/规划的最小集：pypdf/openpyxl/python-docx/bs4。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Part:
    text: str
    page_no: int = 0
    kind: str = "paragraph"          # heading/paragraph/table/image_text/unsupported
    meta: dict = field(default_factory=dict)

    @property
    def clean(self) -> str:
        return self.text.strip()


class ParseError(RuntimeError):
    pass


# 解析器延迟导入，避免 import 本模块即拉入全部依赖
_PARSERS: dict[str, object] = {}


def _get_parser(kind: str):
    if kind in _PARSERS:
        return _PARSERS[kind]
    if kind == "pdf":
        from .pdf_parser import PdfParser
        _PARSERS[kind] = PdfParser()
    elif kind == "docx":
        from .docx_parser import DocxParser
        _PARSERS[kind] = DocxParser()
    elif kind in ("txt", "md"):
        from .text_parser import TextParser
        _PARSERS[kind] = TextParser()
    elif kind == "csv":
        from .csv_parser import CsvParser
        _PARSERS[kind] = CsvParser()
    elif kind == "xlsx":
        from .xlsx_parser import XlsxParser
        _PARSERS[kind] = XlsxParser()
    elif kind == "html":
        from .html_parser import HtmlParser
        _PARSERS[kind] = HtmlParser()
    elif kind == "image":
        from .image_parser import ImageParser
        _PARSERS[kind] = ImageParser()
    else:
        raise ParseError(f"不支持的解析类型: {kind}")
    return _PARSERS[kind]


def parse_file(path: Path, kind: str) -> list[Part]:
    parser = _get_parser(kind)
    return parser.parse(path)
