from contextlib import ExitStack, closing

from openpyxl import load_workbook

from app.services.content_budget import ContentBudget
from app.services.document_resource_errors import DocumentResourceError
from app.services.resource_limits import upload_limits
from app.services.upload_validation import validate_document_source


def extract_content_from_excel(file_path):
    validate_document_source(file_path, ".xlsx")
    limits = upload_limits()
    budget = ContentBudget()
    blocks = []
    rows = cells = 0
    # Two streaming views preserve both formula text and cached values without
    # materializing two complete workbook models or retaining external links.
    with ExitStack() as stack:
        formulas = stack.enter_context(closing(load_workbook(file_path, read_only=True, data_only=False, keep_links=False)))
        values = stack.enter_context(closing(load_workbook(file_path, read_only=True, data_only=True, keep_links=False)))
        if len(formulas.sheetnames) > limits.worksheets:
            raise DocumentResourceError("spreadsheet_limit")
        for formula_sheet in formulas.worksheets:
            sheet_name = formula_sheet.title
            value_sheet = values[sheet_name]
            # Preflight checked actual coordinates; do not trust understated XML dimensions.
            formula_sheet.reset_dimensions()
            value_sheet.reset_dimensions()
            value_rows = value_sheet.iter_rows()
            for row_number, formula_row in enumerate(formula_sheet.iter_rows(), start=1):
                rows += 1
                cells += len(formula_row)
                if (row_number > limits.sheet_rows or rows > limits.total_rows
                        or len(formula_row) > limits.columns or cells > limits.cells):
                    raise DocumentResourceError("spreadsheet_limit")
                value_row = next(value_rows, ())
                parts = []
                for column_number, cell in enumerate(formula_row):
                    value = cell.value
                    if value is None:
                        continue
                    cached = value_row[column_number].value if column_number < len(value_row) else None
                    part = (f"{cell.coordinate}: Formula={value}, Value={cached}"
                            if isinstance(value, str) and value.startswith("=") else f"{cell.coordinate}: {value}")
                    # Bound before joining a whole row.
                    budget.text(part + (" | " if parts else ""))
                    parts.append(part)
                if parts:
                    budget.blocks += 1
                    if budget.blocks > limits.blocks:
                        raise DocumentResourceError("content_limit")
                    blocks.append({"type": "table", "content": " | ".join(parts),
                                   "location": f"{sheet_name} - Row {row_number}",
                                   "metadata": {"sheet": sheet_name, "row": row_number}})
    return blocks
