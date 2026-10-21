"""HTML 转为纯文本与表格，按文档顺序保留正文，不执行脚本或请求远程资源。"""
from __future__ import annotations
from pathlib import Path
from . import ParseError, Part

class HtmlParser:
    def parse(self, path: Path) -> list[Part]:
        try:
            from bs4 import BeautifulSoup, NavigableString, Tag, Comment
        except ImportError as exc:
            raise ParseError("缺少 beautifulsoup4 依赖") from exc
        try:
            soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
        except Exception as exc:
            raise ParseError(f"HTML 解析失败: {exc}") from exc
        for tag in soup(["script", "style", "nav", "footer", "head", "template"]):
            tag.decompose()
        parts: list[Part] = []
        headings = {"h1", "h2", "h3", "h4", "h5", "h6"}
        blocks = {"p", "li", "pre", "blockquote", "dt", "dd"}
        def visit(node):
            if isinstance(node, Comment):
                return
            if isinstance(node, NavigableString):
                text = " ".join(str(node).split())
                if text:
                    parts.append(Part(text=text))
                return
            if not isinstance(node, Tag):
                return
            if node.name == "table":
                rows = []
                for row in node.find_all("tr"):
                    cells = [" ".join(cell.get_text(" ", strip=True).split()) for cell in row.find_all(["th", "td"], recursive=False)]
                    if any(cells):
                        rows.append(" | ".join(cells))
                if rows:
                    parts.append(Part(text="\n".join(rows), kind="table", meta={"rows": len(rows)}))
                return
            if node.name in headings | blocks:
                text = node.get_text(" ", strip=True)
                if text:
                    parts.append(Part(text=text, kind="heading" if node.name in headings else "paragraph"))
                return
            for child in node.children:
                visit(child)
        visit(soup.body or soup)
        return parts
