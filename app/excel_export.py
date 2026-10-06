"""Eksport słownika (razem z Twoimi tłumaczeniami roboczymi) do Excela."""
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from app.dictionary import TYPE_LABELS, ParameterDictionary
from app.products import ProductIndex
from app.storage import DATA_DIRECTORY

MAX_COLUMN_WIDTH = 50
MAX_PRODUCTS_IN_CELL = 20


def export_to_excel(dictionary: ParameterDictionary, drafts: dict[int, str],
                    products: ProductIndex | None = None) -> Path:
    workbook = Workbook()
    parameters_sheet = workbook.active
    parameters_sheet.title = "Parametry"
    parameters_sheet.append(["ID", "Typ", "Nazwa (pol)", "Nazwa (eng)", "Stan PL", "Stan EN", "Liczba wartości",
                             "Wartości do tłumaczenia", "Wartości z brakami PL", "Tłumaczenie EN"])
    for parameter in dictionary.parameters:
        parameters_sheet.append([parameter.id, TYPE_LABELS.get(parameter.type, parameter.type), parameter.name_pl,
                                 parameter.name_en, parameter.polish_state.value, parameter.state.value,
                                 dictionary.value_count_of(parameter.id), dictionary.untranslated_values_of(parameter.id),
                                 dictionary.polish_problem_values_of(parameter.id), drafts.get(parameter.id, "")])

    values_sheet = workbook.create_sheet("Wartości")
    product_headers = ["Produkty", "Kategorie", "Produkty (nazwy)"] if products else []
    values_sheet.append(["Parametr (pol)", "Wartość (pol)", "Parametr (eng)", "Wartość (eng)", "Stan PL", "Stan EN",
                         *product_headers, "ID wartości", "ID parametru", "Tłumaczenie EN"])
    for value in dictionary.values:
        product_cells = []
        if products:
            items = products.products_with(value.id)
            names = "; ".join(p.name for p in items[:MAX_PRODUCTS_IN_CELL])
            if len(items) > MAX_PRODUCTS_IN_CELL:
                names += f"; … i {len(items) - MAX_PRODUCTS_IN_CELL} więcej"
            product_cells = [len(items), ", ".join(products.categories_of(value.id)), names]
        values_sheet.append([dictionary.parent_name(value), value.name_pl, dictionary.parent_name_en(value),
                             value.name_en, value.polish_state.value, value.state.value, *product_cells,
                             value.id, value.parameter_id, drafts.get(value.id, "")])

    for sheet in (parameters_sheet, values_sheet):
        _format_sheet(sheet)

    DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = DATA_DIRECTORY / f"slownik_parametrow_{datetime.now():%Y-%m-%d_%H%M}.xlsx"
    workbook.save(path)
    return path


def _format_sheet(sheet) -> None:
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        longest = max(len(str(cell.value or "")) for cell in column)
        sheet.column_dimensions[column[0].column_letter].width = min(longest + 2, MAX_COLUMN_WIDTH)
