"""Modele tabel (wzorzec Qt „model/view”).

- DictionaryTableModel - podaje tabeli dane: ile wierszy, co jest w komórce, jaki kolor, co można edytować.
- DictionaryFilterProxy - „nakładka” na model: ukrywa wiersze niepasujące do filtrów i sortuje,
  nie zmieniając samych danych. Tabela pokazuje nakładkę, nie model bezpośrednio.
"""
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QFont

from app.dictionary import DictionaryElement, PolishState, TranslationState
from app.labels import NEW_EN_TOOLTIP
from app.natural_sort import SORT_ROLE, NaturalSortProxy

STATE_COLORS = {
    TranslationState.MISSING: QColor("#c62828"),
    TranslationState.SAME_AS_POLISH: QColor("#c62828"),
    TranslationState.PLACEHOLDER: QColor("#e65100"),
    TranslationState.NUMBER_OR_CODE: QColor("#757575"),
    TranslationState.TRANSLATED: QColor("#2e7d32"),
}
POLISH_STATE_COLORS = {
    PolishState.OK: QColor("#757575"),
    PolishState.EMPTY: QColor("#c62828"),
    PolishState.PLACEHOLDER: QColor("#c62828"),
    PolishState.NONE_VALUE: QColor("#e65100"),
}
TRANSLATION_TOOLTIP = NEW_EN_TOOLTIP


@dataclass(frozen=True)
class Column:
    header: str
    value: Callable[[DictionaryElement], object] | None = None   # None = kolumna z Twoim tłumaczeniem
    is_state: bool = False
    is_polish_state: bool = False
    tooltip: Callable[[DictionaryElement], str] | None = None   # dymek po najechaniu myszką
    color: Callable[[DictionaryElement], QColor | None] | None = None   # kolor tekstu w komórce

    @property
    def is_translation(self) -> bool:
        return self.value is None


def cell_data(element: DictionaryElement, column: Column, drafts: dict[int, str], role):
    """Co pokazać w komórce - wspólne dla tabeli parametrów i drzewa parametrów z wartościami."""
    value = drafts.get(element.id, "") if column.is_translation else column.value(element)
    if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole, SORT_ROLE):
        return value
    if role == Qt.ItemDataRole.ForegroundRole:
        if column.is_state:
            return STATE_COLORS.get(element.state)
        if column.is_polish_state:
            return POLISH_STATE_COLORS.get(element.polish_state)
        if column.color:
            return column.color(element)
    if role == Qt.ItemDataRole.FontRole and column.is_translation and value:
        font = QFont()
        font.setBold(True)
        return font
    if role == Qt.ItemDataRole.ToolTipRole:
        if column.is_translation:
            return TRANSLATION_TOOLTIP
        if column.tooltip:
            return column.tooltip(element)
    return None


def update_draft(drafts: dict[int, str], element: DictionaryElement, value) -> bool:
    """Zapisuje tłumaczenie robocze w słowniku drafts. Zwraca True, gdy coś się zmieniło."""
    text = str(value or "").strip()
    if text == drafts.get(element.id, ""):
        return False
    if text:
        drafts[element.id] = text
    else:
        drafts.pop(element.id, None)   # puste pole = rezygnacja z tłumaczenia
    return True


class DictionaryTableModel(QAbstractTableModel):
    def __init__(self, drafts: dict[int, str], on_draft_changed: Callable[[], None], parent=None):
        super().__init__(parent)
        self._drafts = drafts                  # wspólny słownik dla obu zakładek
        self._on_draft_changed = on_draft_changed
        self._elements: list[DictionaryElement] = []
        self._columns: list[Column] = []
        self._search_texts: dict[int, str] = {}

    def set_content(self, elements: list[DictionaryElement], columns: list[Column],
                    extra_search_text: Callable[[DictionaryElement], str] | None = None) -> None:
        """extra_search_text - dodatkowy tekst do wyszukiwania, którego nie widać w kolumnach
        (np. nazwy WSZYSTKICH produktów z daną wartością, a nie tylko przykładowego)."""
        self.beginResetModel()   # Qt musi wiedzieć, że dane zmieniają się w całości
        self._elements, self._columns = elements, columns
        self._search_texts = {}
        for element in elements:
            parts = [str(column.value(element)) for column in columns if not column.is_translation]
            if extra_search_text:
                parts.append(extra_search_text(element))
            self._search_texts[element.id] = " ".join(parts).lower()
        self.endResetModel()

    # --- pomocnicze, używane przez filtr i okno ---
    def element_at(self, row: int) -> DictionaryElement:
        return self._elements[row]

    def column(self, number: int) -> Column:
        return self._columns[number]

    def has_draft(self, element: DictionaryElement) -> bool:
        return element.id in self._drafts

    def matches_text(self, element: DictionaryElement, text: str) -> bool:
        return text in self._search_texts.get(element.id, "") or text in self._drafts.get(element.id, "").lower()

    # --- metody wymagane przez Qt (nazwy w camelCase, bo nadpisujemy metody Qt) ---
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._elements)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._columns[section].header
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        return cell_data(self._elements[index.row()], self._columns[index.column()], self._drafts, role)

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and self._columns[index.column()].is_translation:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
        """Wywoływane przez Qt, gdy zatwierdzisz edycję komórki."""
        if role != Qt.ItemDataRole.EditRole or not self._columns[index.column()].is_translation:
            return False
        if not update_draft(self._drafts, self._elements[index.row()], value):
            return False
        self.dataChanged.emit(index.siblingAtColumn(0), index.siblingAtColumn(len(self._columns) - 1))
        self._on_draft_changed()
        return True


@dataclass(frozen=True)
class StateFilter:
    label: str
    accepts: Callable[[DictionaryElement, bool], bool]   # (element, czy ma moje tłumaczenie) -> pokazać?


STATE_FILTERS = [
    StateFilter("Wszystkie", lambda element, has_draft: True),
    StateFilter("Do tłumaczenia", lambda element, has_draft: element.needs_translation),
    StateFilter("Do tłumaczenia – nic jeszcze nie wpisane",
                lambda element, has_draft: element.needs_translation and not has_draft),
    StateFilter("Z wpisanym nowym angielskim (niewysłane)", lambda element, has_draft: has_draft),
    StateFilter("Braki po polsku (pusta / dane do uzupełnienia)",
                lambda element, has_draft: element.has_polish_problem),
    StateFilter("Po polsku „brak” – do sprawdzenia",
                lambda element, has_draft: element.polish_state is PolishState.NONE_VALUE),
    # state=state „zamraża” bieżący stan w lambdzie (inaczej wszystkie wskazywałyby ostatni)
    *(StateFilter(f"Status: {state.value}", lambda element, has_draft, state=state: element.state is state)
      for state in TranslationState),
]


class DictionaryFilterProxy(NaturalSortProxy):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._state_filter = STATE_FILTERS[0]
        self._parameter_id: int | None = None
        self._category: str | None = None
        # funkcja „ID wartości -> kategorie jej produktów”; ustawiana, gdy są pobrane produkty
        self.category_lookup: Callable[[int], tuple[str, ...]] | None = None
        self.used_value_ids: set[int] | None = None   # wartości użyte w pobranych produktach
        self._only_used = False

    def set_filters(self, text: str, state_filter: StateFilter, parameter_id: int | None,
                    category: str | None = None, only_used: bool = False) -> None:
        def change():
            self._text = text.strip().lower()
            self._state_filter = state_filter
            self._parameter_id = parameter_id
            self._category = category
            self._only_used = only_used
        self.refilter(change)

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        model: DictionaryTableModel = self.sourceModel()
        element = model.element_at(source_row)
        if self._parameter_id is not None and element.parameter_id != self._parameter_id:
            return False
        if not self._state_filter.accepts(element, model.has_draft(element)):
            return False
        if self._only_used and (self.used_value_ids is None or element.id not in self.used_value_ids):
            return False
        if self._category and (self.category_lookup is None or self._category not in self.category_lookup(element.id)):
            return False
        return not self._text or model.matches_text(element, self._text)
