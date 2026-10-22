"""Read legacy XLS values without executing macros or recalculating formulas."""
from pathlib import Path

from openpyxl.utils import get_column_letter

from src.attachments.parsers.excel import CHUNK_CHARS, _sheet_chunk


def parse(path: Path, settings) -> tuple:
    try:
        import xlrd
    except ImportError:
        raise ValueError('读取 .xls 需要安装 legacy-excel 可选依赖，或先另存为 .xlsx。') from None
    book = xlrd.open_workbook(str(path), on_demand=True)
    chunks, count = [], 0
    try:
        if book.nsheets > settings.workbook_max_sheets:
            raise ValueError('工作表数量超过限制。')
        for sheet in book.sheets():
            buffer, size, start, end = [], 0, 0, 0
            for row in range(sheet.nrows):
                values = []
                for col, cell in enumerate(sheet.row(row)):
                    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                        continue
                    count += 1
                    if count > settings.workbook_max_nonempty_cells:
                        raise ValueError('非空单元格数量超过限制。')
                    value = cell.value
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        value = xlrd.xldate_as_datetime(value, book.datemode).isoformat()
                    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        value = bool(value)
                    elif cell.ctype == xlrd.XL_CELL_ERROR:
                        value = xlrd.error_text_from_code.get(value, '#ERROR')
                    values.append(f'{get_column_letter(col+1)}{row+1}={value}')
                if not values:
                    continue
                line = f'row {row+1}: ' + ' | '.join(values) + '\n'
                if buffer and size + len(line) > CHUNK_CHARS:
                    chunks.append(_sheet_chunk(sheet.name, start, end, ''.join(buffer)))
                    buffer, size = [], 0
                if not buffer:
                    start = row+1
                buffer.append(line)
                size += len(line)
                end = row+1
            if buffer:
                chunks.append(_sheet_chunk(sheet.name, start, end, ''.join(buffer)))
        if not chunks:
            chunks.append(_sheet_chunk('(empty)', 1, 1, '(workbook has no non-empty cells)'))
        return chunks, ['旧版 Excel 读取保存的单元格值，不重新计算公式或执行宏。'], {
            'sheets': book.nsheets, 'nonempty_cells': count}
    finally:
        book.release_resources()
