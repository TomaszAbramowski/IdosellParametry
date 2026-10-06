"""Excel w obie strony - OSOBNY plik dla każdego trybu:

- tryb 1: arkusz „Parametry” - nazwy parametrów i sekcji,
- tryb 2: arkusz „Wartości”  - nazwy wartości (z parametrem PL/EN obok, dla kontekstu).

Do pliku trafiają wiersze WIDOCZNE w zakładce (czyli z uwzględnieniem filtrów, np. „Do tłumaczenia”).
W żółtej kolumnie „Po angielsku – NOWE” wpisujesz, jak ma być. Puste pole = bez zmian.

Wgrywanie czyta tylko ID i „Nowa nazwa EN” i porównuje z tym, co jest w sklepie.
Do podglądu trafiają WYŁĄCZNIE wiersze, w których nowa nazwa różni się od obecnej.
Kolumny są szukane po nagłówkach, więc ich kolejność w Excelu nie ma znaczenia.
"""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from app import labels
from app.changes import Change, make_change
from app.dictionary import TYPE_LABELS, TYPE_VALUE, DictionaryElement, ParameterDictionary
from app.products import ProductIndex
from app.storage import DATA_DIRECTORY

PARAMETERS_SHEET = "Parametry"
VALUES_SHEET = "Wartości"
INSTRUCTIONS_SHEET = "Instrukcja"
ID_HEADER = "Nr (ID)"
PARENT_ID_HEADER = "Nr parametru (ID)"
EN_AT_EXPORT_HEADER = labels.CURRENT_EN
NEW_EN_HEADER = labels.NEW_EN
# nazwy kolumn ze starszych plików - nadal rozpoznawane przy wgrywaniu
LEGACY_HEADERS = {"ID": ID_HEADER,
                  "EN w sklepie": EN_AT_EXPORT_HEADER, "EN teraz w sklepie": EN_AT_EXPORT_HEADER,
                  "Nowa nazwa EN": NEW_EN_HEADER, "NOWE tłumaczenie EN": NEW_EN_HEADER}
MAX_COLUMN_WIDTH = 45
HEADER_SEARCH_ROWS = 5            # w których pierwszych wierszach szukać nagłówków

# Trzy strefy arkusza: co tylko czytasz, gdzie wpisujesz, czego nie ruszasz
READ, EDIT, TECH = "read", "edit", "tech"
ZONE_TITLES = {READ: "PODGLĄD – tak jest TERAZ w sklepie (tu nic nie zmieniaj)",
               EDIT: "✎ TUTAJ WPISZ nowy angielski",
               TECH: "Numery – nie zmieniaj"}
EDIT_HEADER_COMMENT = (f"Wpisz, jak ma być po angielsku. Zastąpi tekst z kolumny „{labels.CURRENT_EN}” "
                       "w tym samym wierszu (tylko angielski – polski zostaje). Puste pole = bez zmian.\n"
                       "Przykład: 5-cio strzałowy → 5-shot")
ZONE_HEADER_FILLS = {READ: PatternFill("solid", start_color="D9D9D9"), EDIT: PatternFill("solid", start_color="FFC000"),
                     TECH: PatternFill("solid", start_color="BFBFBF")}
ZONE_CELL_FILLS = {READ: None, EDIT: PatternFill("solid", start_color="FFF2CC"),
                   TECH: PatternFill("solid", start_color="F2F2F2")}
RED_FONT = Font(color="C00000")
GREY_FONT = Font(color="808080")
EDIT_BORDER = Border(left=Side(style="medium", color="C65911"), right=Side(style="medium", color="C65911"))


@dataclass(frozen=True)
class ExcelColumn:
    header: str
    value: Callable[[DictionaryElement], object]
    zone: str = READ
    red_when_todo: bool = False      # czerwony tekst, gdy element jest do przetłumaczenia
    width: int | None = None


def _instructions(sheet_name: str, what: str) -> list[str]:
    return [
        f"Tłumaczenie na angielski: {what}",
        "",
        "CO ROBISZ: w żółtej kolumnie wpisujesz poprawny tekst po angielsku. Nic więcej.",
        "",
        f"W arkuszu „{sheet_name}” są trzy strefy (opisane w pierwszym wierszu):",
        "   • SZARA „PODGLĄD” – tak jest TERAZ w sklepie: tekst po polsku i obecny tekst po angielsku.",
        "     Czerwony tekst po angielsku = jeszcze nieprzetłumaczone (np. jest tam polskie słowo).",
        f"   • ŻÓŁTA „{NEW_EN_HEADER}” – wpisz, jak ma być po angielsku.",
        f"     To, co wpiszesz, zastąpi tekst z kolumny „{EN_AT_EXPORT_HEADER}” w tym samym wierszu.",
        "     Polski tekst się NIE zmienia. Puste pole = bez zmian.",
        "     Przykład:  5-cio strzałowy  |  teraz: 5-cio strzałowy  |  NOWE: 5-shot",
        "   • „Numery” – nie zmieniaj, po nich program rozpoznaje, który to wiersz.",
        "",
        "Wiersze możesz sortować, filtrować i usuwać – liczy się ID.",
        "Zapisz plik i w programie, w tej samej zakładce, kliknij „Wgraj z Excela”.",
        "Program pokaże TYLKO te wiersze, w których nowe EN różni się od tego, co jest w sklepie.",
        "Nic nie trafi do IdoSella, dopóki nie klikniesz „Wyślij” w podglądzie.",
        "",
        "Nie trzeba tłumaczyć liczb z jednostkami (np. „44.2 J / 32.6 ft.lbs”, „5.5 mm (.22)”, „200 bar”) –",
        "są takie same po polsku i po angielsku. Program oznacza je jako „Liczba / jednostka”.",
        "",
        "Uwaga: jedna zmiana w słowniku zmienia nazwę we WSZYSTKICH produktach, które używają danego elementu.",
    ]


def _new_workbook(sheet_name: str, what: str):
    workbook = Workbook()
    instructions = workbook.active
    instructions.title = INSTRUCTIONS_SHEET
    for line in _instructions(sheet_name, what):
        instructions.append([line])
    instructions["A1"].font = Font(bold=True, size=14)
    instructions.column_dimensions["A"].width = 120
    sheet = workbook.create_sheet(sheet_name)
    workbook.active = workbook.index(sheet)        # plik otwiera się od razu na tabeli
    return workbook, sheet


def _save(workbook, file_prefix: str) -> Path:
    DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = DATA_DIRECTORY / f"{file_prefix}_{datetime.now():%Y-%m-%d_%H%M}.xlsx"
    workbook.save(path)
    return path


def _write_table(sheet, columns: list[ExcelColumn], elements: list[DictionaryElement]) -> None:
    """Wiersz 1: strefy, wiersz 2: nagłówki, od wiersza 3: dane."""
    # --- wiersz 1: opis stref (scalone komórki nad kolumnami tej samej strefy) ---
    start = 0
    while start < len(columns):
        end = start
        while end + 1 < len(columns) and columns[end + 1].zone == columns[start].zone:
            end += 1
        zone = columns[start].zone
        cell = sheet.cell(row=1, column=start + 1, value=ZONE_TITLES[zone])
        cell.font = Font(bold=True)
        cell.fill = ZONE_HEADER_FILLS[zone]
        cell.alignment = Alignment(horizontal="center")
        if end > start:
            sheet.merge_cells(start_row=1, start_column=start + 1, end_row=1, end_column=end + 1)
        start = end + 1
    # --- wiersz 2: nagłówki ---
    for number, column in enumerate(columns, start=1):
        cell = sheet.cell(row=2, column=number, value=column.header)
        cell.font = Font(bold=True)
        cell.fill = ZONE_HEADER_FILLS[column.zone]
        if column.zone == EDIT:
            cell.border = EDIT_BORDER
            cell.comment = Comment(EDIT_HEADER_COMMENT, "Program")
    # --- dane ---
    for row, element in enumerate(elements, start=3):
        for number, column in enumerate(columns, start=1):
            cell = sheet.cell(row=row, column=number, value=column.value(element))
            fill = ZONE_CELL_FILLS[column.zone]
            if fill:
                cell.fill = fill
            if column.zone == EDIT:
                cell.number_format = "@"          # tekst - Excel nie zamieni „1/2” na datę
                cell.border = EDIT_BORDER
            elif column.zone == TECH:
                cell.font = GREY_FONT
            elif column.red_when_todo and element.needs_translation:
                cell.font = RED_FONT
    # --- wygląd ---
    sheet.page_setup.orientation = "landscape"        # wydruk: cała szerokość tabeli na jednej stronie
    sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight = 1, 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_title_rows = "1:2"
    sheet.freeze_panes = "B3"
    sheet.auto_filter.ref = f"A2:{sheet.cell(row=2, column=len(columns)).column_letter}{max(2, sheet.max_row)}"
    for number, column in enumerate(columns, start=1):
        letter = sheet.cell(row=2, column=number).column_letter
        longest = max([len(str(column.header))] + [len(str(sheet.cell(row=r, column=number).value or ""))
                                                    for r in range(3, sheet.max_row + 1)])
        sheet.column_dimensions[letter].width = column.width or min(longest + 2, MAX_COLUMN_WIDTH)


def export_parameters(dictionary: ParameterDictionary, drafts: dict[int, str],
                      parameters: list[DictionaryElement]) -> Path:
    """Tryb 1: parametry i sekcje (te, które są widoczne w zakładce)."""
    workbook, sheet = _new_workbook(PARAMETERS_SHEET, "nazwy parametrów i sekcji PL → EN")
    columns = [
        ExcelColumn("Typ", lambda e: TYPE_LABELS.get(e.type, e.type)),
        ExcelColumn(labels.NAME_PL, lambda e: e.name_pl),
        ExcelColumn(EN_AT_EXPORT_HEADER, lambda e: e.name_en, red_when_todo=True),
        ExcelColumn(labels.STATUS, lambda e: e.state.value),
        ExcelColumn(NEW_EN_HEADER, lambda e: drafts.get(e.id, ""), zone=EDIT, width=40),
        ExcelColumn(ID_HEADER, lambda e: e.id, zone=TECH, width=12),
    ]
    _write_table(sheet, columns, parameters)
    return _save(workbook, "tlumaczenie_parametrow")


def export_values(dictionary: ParameterDictionary, drafts: dict[int, str], values: list[DictionaryElement],
                  products: ProductIndex | None = None) -> Path:
    """Tryb 2: wartości (te, które są widoczne w zakładce), z parametrem dla kontekstu.
    products - nieużywane (towary są w trybie 3), zostaje dla zgodności."""
    workbook, sheet = _new_workbook(VALUES_SHEET, "nazwy wartości parametrów PL → EN")
    columns = [
        ExcelColumn(labels.PARAMETER_PL, lambda e: dictionary.parent_name(e)),
        ExcelColumn(labels.PARAMETER_CURRENT_EN, lambda e: dictionary.parent_name_en(e)),
        ExcelColumn(labels.VALUE_PL, lambda e: e.name_pl),
        ExcelColumn(EN_AT_EXPORT_HEADER, lambda e: e.name_en, red_when_todo=True),
        ExcelColumn(labels.STATUS, lambda e: e.state.value),
        ExcelColumn(NEW_EN_HEADER, lambda e: drafts.get(e.id, ""), zone=EDIT, width=40),
        ExcelColumn(ID_HEADER, lambda e: e.id, zone=TECH, width=12),
        ExcelColumn(PARENT_ID_HEADER, lambda e: e.parameter_id, zone=TECH, width=18),
    ]
    _write_table(sheet, columns, values)
    return _save(workbook, "tlumaczenie_wartosci")


# ======================= wgrywanie =======================

@dataclass
class ExcelImport:
    changes: list[Change]        # tylko różnice względem sklepu
    unchanged: int               # wypełnione, ale takie same jak w sklepie
    problems: list[str]          # np. nieznane ID, brak kolumn


def _cell_text(value) -> str:
    """Komórka -> tekst. Excel potrafi zapisać „129” jako liczbę 129.0 - zamieniamy na „129”."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


class WrongFileError(Exception):
    """Plik nie jest z tego trybu (np. plik wartości wgrywany w zakładce parametrów)."""


def read_changes_from_excel(path: Path, dictionary: ParameterDictionary, sheet_name: str) -> ExcelImport:
    """sheet_name: PARAMETERS_SHEET (tryb 1) albo VALUES_SHEET (tryb 2)."""
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet_name not in workbook.sheetnames:
            other = VALUES_SHEET if sheet_name == PARAMETERS_SHEET else PARAMETERS_SHEET
            hint = (f" To wygląda na plik z arkuszem „{other}” – wgraj go w drugiej zakładce."
                    if other in workbook.sheetnames else "")
            raise WrongFileError(f"W pliku nie ma arkusza „{sheet_name}”.{hint}")
        result = ExcelImport([], 0, [])
        _read_sheet(workbook[sheet_name], sheet_name, dictionary, result, set())
        return result
    finally:
        workbook.close()


def _read_sheet(sheet, sheet_name: str, dictionary: ParameterDictionary, result: ExcelImport, seen: set[int]):
    rows = sheet.iter_rows(values_only=True)
    headers, header_row = [], 0
    for header_row, row in enumerate(rows, start=1):     # nagłówki: wiersz z „ID” wśród pierwszych wierszy
        candidate = [LEGACY_HEADERS.get(_cell_text(h), _cell_text(h)) for h in row]
        if ID_HEADER in candidate:
            headers = candidate
            break
        if header_row >= HEADER_SEARCH_ROWS:
            break
    if ID_HEADER not in headers or NEW_EN_HEADER not in headers:
        result.problems.append(f"Arkusz „{sheet_name}”: brak kolumny „{ID_HEADER}” albo „{NEW_EN_HEADER}”.")
        return
    id_column, new_column = headers.index(ID_HEADER), headers.index(NEW_EN_HEADER)
    old_column = headers.index(EN_AT_EXPORT_HEADER) if EN_AT_EXPORT_HEADER in headers else None

    for row_number, row in enumerate(rows, start=header_row + 1):
        new_en = _cell_text(row[new_column]) if new_column < len(row) else ""
        if not new_en:
            continue                                  # puste = bez zmian
        raw_id = _cell_text(row[id_column]) if id_column < len(row) else ""
        if not raw_id.isdigit():
            result.problems.append(f"Arkusz „{sheet_name}”, wiersz {row_number}: brak poprawnego ID – pominięty.")
            continue
        element = dictionary.elements.get(int(raw_id))
        if element is None:
            result.problems.append(f"Arkusz „{sheet_name}”, wiersz {row_number}: ID {raw_id} nie istnieje "
                                   f"w pobranym słowniku – pominięty.")
            continue
        if (element.type == TYPE_VALUE) != (sheet_name == VALUES_SHEET):
            result.problems.append(f"Arkusz „{sheet_name}”, wiersz {row_number}: ID {element.id} to "
                                   f"{TYPE_LABELS.get(element.type, element.type)} – nie pasuje do tego arkusza.")
            continue
        if element.id in seen:
            result.problems.append(f"ID {element.id} występuje w pliku więcej niż raz – wzięty pierwszy wpis.")
            continue
        seen.add(element.id)
        if new_en == element.name_en:
            result.unchanged += 1
            continue
        note = ""
        if old_column is not None and old_column < len(row):
            en_at_export = _cell_text(row[old_column])
            if en_at_export != element.name_en:
                note = f"W sklepie zmieniło się od zgrania pliku (było „{en_at_export}”)"
        result.changes.append(make_change(element, dictionary, new_en, note))
