"""CSV 表格解析，保留列名与行号以便检索和核对。"""
from __future__ import annotations
import csv
import io
from pathlib import Path
from . import Part, ParseError

class CsvParser:
    def parse(self, path: Path) -> list[Part]:
        raw = path.read_bytes()
        text = None
        for encoding in ('utf-8-sig', 'gb18030'):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ParseError('CSV 编码无法识别，请另存为 UTF-8 CSV 后重试')
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        try:
            rows = csv.reader(io.StringIO(text), dialect)
            header = next(rows, [])
            if not any(cell.strip() for cell in header):
                raise ParseError('CSV 文件没有有效列名')
            parts = [Part(text='表格列名：' + '；'.join(header), kind='table', meta={'row': 1})]
            for number, row in enumerate(rows, start=2):
                if not any(c.strip() for c in row):
                    continue
                values = [f'{header[i] if i < len(header) else "列" + str(i+1)}：{value.strip()}' for i, value in enumerate(row) if value.strip()]
                parts.append(Part(text=f'第 {number} 行：' + '；'.join(values), kind='table', meta={'row': number}))
            return parts
        except (csv.Error, UnicodeError) as exc:
            raise ParseError('CSV 结构不正确，请检查引号与分隔符') from exc
