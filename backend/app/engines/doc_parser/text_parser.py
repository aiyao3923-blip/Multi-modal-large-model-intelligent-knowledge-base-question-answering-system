"""文本/markdown 解析：按行保序；markdown 标题转换为 heading。"""
from __future__ import annotations

from pathlib import Path

from . import ParseError, Part


class TextParser:
    def parse(self, path: Path) -> list[Part]:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise ParseError(f"文本读取失败: {exc}") from exc
        parts: list[Part] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                parts.append(Part(text=stripped, kind="heading"))
            else:
                parts.append(Part(text=stripped, kind="paragraph"))
        return parts
