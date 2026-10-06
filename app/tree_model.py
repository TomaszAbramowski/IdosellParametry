"""Model drzewa „parametr -> jego wartości” (tryb 2).

Dwa poziomy:  wiersz główny = parametr,  wiersze pod nim = wartości tego parametru.
Qt rozpoznaje poziom po „internalId” indeksu:
    0       -> parametr (wiersz główny)
    n + 1   -> wartość parametru, który jest w wierszu głównym nr n
"""
from typing import Callable

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt
from PySide6.QtGui import QFont

from app.dictionary import TYPE_VALUE, DictionaryElement
from app.natural_sort import NaturalSortProxy
from app.table_model import STATE_FILTERS, Column, StateFilter, cell_data, update_draft

TOP_LEVEL = 0


class ParameterTreeModel(QAbstractItemModel):
    def __init__(self, drafts: dict[int, str], on_draft_changed: Callable[[], None], parent=None):
        super().__init__(parent)
        self._drafts = drafts
        self._on_draft_changed = on_draft_changed
        self._parameters: list[DictionaryElement] = []
        self._children: dict[int, list[DictionaryElement]] = {}
        self._columns: list[Column] = []
        self._search_texts: dict[int, str] = {}
        self._positions: dict[int, tuple[int | None, int]] = {}   # ID -> (wiersz rodzica albo None, wiersz)

    def set_content(self, parameters: list[DictionaryElement], children: dict[int, list[DictionaryElement]],
                    columns: list[Column], extra_search_text: Callable[[DictionaryElement], str] | None = None):
        self.beginResetModel()
        self._parameters, self._children, self._columns = parameters, children, columns
        self._search_texts, self._positions = {}, {}
        for parameter_row, parameter in enumerate(parameters):
            self._remember(parameter, None, parameter_row, extra_search_text)
            for value_row, value in enumerate(children.get(parameter.id, [])):
                self._remember(value, parameter_row, value_row, extra_search_text)
        self.endResetModel()

    def _remember(self, element, parent_row, row, extra_search_text) -> None:
        self._positions[element.id] = (parent_row, row)
        parts = [str(column.value(element)) for column in self._columns if not column.is_translation]
        if extra_search_text:
            parts.append(extra_search_text(element))
        self._search_texts[element.id] = " ".join(parts).lower()

    # --- pomocnicze ---
    def element_at(self, index: QModelIndex) -> DictionaryElement:
        if index.internalId() == TOP_LEVEL:
            return self._parameters[index.row()]
        parameter = self._parameters[index.internalId() - 1]
        return self._children[parameter.id][index.row()]

    def children_of(self, parameter_id: int) -> list[DictionaryElement]:
        return self._children.get(parameter_id, [])

    def index_of(self, element_id: int) -> QModelIndex:
        if element_id not in self._positions:
            return QModelIndex()
        parent_row, row = self._positions[element_id]
        if parent_row is None:
            return self.index(row, 0)
        return self.index(row, 0, self.index(parent_row, 0))

    def has_draft(self, element: DictionaryElement) -> bool:
        return element.id in self._drafts

    def matches_text(self, element: DictionaryElement, text: str) -> bool:
        return text in self._search_texts.get(element.id, "") or text in self._drafts.get(element.id, "").lower()

    # --- metody wymagane przez Qt ---
    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            return self.createIndex(row, column, TOP_LEVEL)
        return self.createIndex(row, column, parent.row() + 1)

    def parent(self, index):
        if not index.isValid() or index.internalId() == TOP_LEVEL:
            return QModelIndex()
        return self.createIndex(index.internalId() - 1, 0, TOP_LEVEL)

    def rowCount(self, parent=QModelIndex()) -> int:
        if not parent.isValid():
            return len(self._parameters)
        if parent.internalId() == TOP_LEVEL and parent.column() == 0:
            return len(self.children_of(self._parameters[parent.row()].id))
        return 0

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self._columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._columns[section].header
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        column = self._columns[index.column()]
        if role == Qt.ItemDataRole.FontRole and index.internalId() == TOP_LEVEL and not column.is_translation:
            font = QFont()
            font.setBold(True)    # parametry pogrubione, wartości zwykłą czcionką
            return font
        return cell_data(self.element_at(index), column, self._drafts, role)

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and self._columns[index.column()].is_translation:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not self._columns[index.column()].is_translation:
            return False
        if not update_draft(self._drafts, self.element_at(index), value):
            return False
        self.dataChanged.emit(index.siblingAtColumn(0), index.siblingAtColumn(len(self._columns) - 1))
        self._on_draft_changed()
        return True


class ParameterTreeProxy(NaturalSortProxy):
    """Filtry drzewa. Zasada: filtry dotyczą przede wszystkim WARTOŚCI;
    parametr jest widoczny, gdy sam pasuje albo gdy pasuje któraś z jego wartości."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRecursiveFilteringEnabled(True)   # rodzic widoczny, gdy pasuje któreś dziecko
        self._text = ""
        self._state_filter: StateFilter = STATE_FILTERS[0]
        self._category: str | None = None
        self._only_used = False
        self.category_lookup: Callable[[int], tuple[str, ...]] | None = None
        self.used_value_ids: set[int] | None = None

    def set_filters(self, text: str, state_filter: StateFilter, category: str | None, only_used: bool) -> None:
        def change():
            self._text = text.strip().lower()
            self._state_filter = state_filter
            self._category = category
            self._only_used = only_used
        self.refilter(change)

    def is_filtering(self) -> bool:
        return bool(self._text or self._category or self._only_used or self._state_filter is not STATE_FILTERS[0])

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        model: ParameterTreeModel = self.sourceModel()
        element = model.element_at(model.index(source_row, 0, source_parent))
        if not self._state_filter.accepts(element, model.has_draft(element)):
            return False
        if element.type != TYPE_VALUE:
            # Parametr sam w sobie nie ma produktów/kategorii - przy tych filtrach widać go tylko przez wartości
            if self._category or self._only_used:
                return False
            return not self._text or model.matches_text(element, self._text)
        if self._only_used and (self.used_value_ids is None or element.id not in self.used_value_ids):
            return False
        if self._category and (self.category_lookup is None or self._category not in self.category_lookup(element.id)):
            return False
        if not self._text:
            return True
        # wpisana nazwa parametru pokazuje wszystkie jego (pasujące do reszty filtrów) wartości
        parent = model.element_at(source_parent)
        return model.matches_text(element, self._text) or model.matches_text(parent, self._text)
