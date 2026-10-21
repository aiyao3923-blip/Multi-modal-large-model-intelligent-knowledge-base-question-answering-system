"""xlsx 解析：工作表 → 表格块（行按列拼接，便于检索）。"""
from __future__ import annotations

from pathlib import Path

from . import ParseError, Part

try:
    import openpyxl  # noqa: F401
except ImportError:  # pragma: no cover
    openpyxl = None


class XlsxParser:
    def parse(self, path: Path) -> list[Part]:
        if openpyxl is None:
            raise ParseError("缺少 openpyxl 依赖")
        try:
            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        except Exception as exc:
            raise ParseError(f"Excel 打开失败: {exc}") from exc
        try:
            parts: list[Part] = []
            for ws in wb.worksheets:
                rows = []
                for row in ws.iter_rows(values_only=True):
                    cells = ["" if c is None else str(c).strip() for c in row]
                    if any(cells):
                        rows.append(" | ".join(cells))
                if rows:
                    parts.append(
                        Part(
                            text="\n".join(rows),
                            kind="table",
                            meta={"sheet": ws.title, "rows": len(rows)},
                        )
                    )
        finally:
            wb.close()
        return parts
