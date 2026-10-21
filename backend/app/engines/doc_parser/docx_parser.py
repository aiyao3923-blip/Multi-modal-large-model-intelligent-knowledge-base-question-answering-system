"""docx 解析：段落 + 标题层级 + 表格结构化。"""
from __future__ import annotations

from pathlib import Path

from . import ParseError, Part


class DocxParser:
    def parse(self, path: Path) -> list[Part]:
        try:
            import docx  # python-docx
        except ImportError as exc:
            raise ParseError("缺少 python-docx 依赖") from exc
        try:
            document = docx.Document(str(path))
        except Exception as exc:
            raise ParseError(f"docx 打开失败: {exc}") from exc
        parts: list[Part] = []
        # 遍历 body 顶层元素，保序输出段落与表格
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        def iter_block_items(parent):
            from docx.oxml.ns import qn
            parent_elm = parent.element.body
            for child in parent_elm.iterchildren():
                if child.tag == qn("w:p"):
                    yield Paragraph(child, parent)
                elif child.tag == qn("w:tbl"):
                    yield Table(child, parent)

        for block in iter_block_items(document):
            if isinstance(block, Paragraph):
                style = (block.style.name or "").lower() if block.style else ""
                text = block.text.strip()
                if not text:
                    continue
                if "heading" in style or text.startswith("#"):
                    parts.append(Part(text=text, kind="heading"))
                else:
                    parts.append(Part(text=text, kind="paragraph"))
            elif isinstance(block, Table):
                rows = []
                for row in block.rows:
                    cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                    rows.append(" | ".join(cells))
                if rows:
                    parts.append(
                        Part(text="\n".join(rows), kind="table",
                             meta={"rows": len(rows), "cols": len(block.columns)})
                    )
        return parts
