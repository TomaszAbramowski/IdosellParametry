"""Tryb 2: tłumaczenie NAZW parametrów i ich wartości - jak w panelu IdoSella, parametr i pod nim wartości.

    ┌ parametry ───────────────────────────────┐ ┌ wartości wybranego parametru ─────────────────────┐
    │ Parametr (PL) │ Parametr (EN) │ Tłum. EN │ │ Wartość (PL) │ Wartość (EN) │ Tłumaczenie EN     │
    └──────────────────────────────────────────┘ └───────────────────────────────────────────────────┘

Tylko nazwy: PL, EN w sklepie i pole do poprawy EN. Czerwony EN = do przetłumaczenia.
Przypisywanie wartości do produktów (i później szablony) jest w trybie 3.
Tłumaczenia trafiają do tych samych tłumaczeń roboczych co w trybie 1 - do sklepu idą dopiero po „Wyślij”.
"""
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemDelegate, QAbstractItemView, QCheckBox, QHeaderView, QLabel, QLineEdit,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from app import labels
from app.dictionary import TYPE_PARAMETER, DictionaryElement, ParameterDictionary
from app.dictionary_tab import SEARCH_DELAY_MS, DictionaryTab
from app.natural_sort import natural_sorted
from app.products import ProductIndex
from app.table_model import STATE_COLORS, TRANSLATION_TOOLTIP, Column

ALL_PARAMETERS_ID = -1
RED = QColor("#c62828")
LIST_HEADERS = [labels.PARAMETER_PL, labels.CURRENT_EN, labels.NEW_EN]
NAME, ENGLISH, TRANSLATION = 0, 1, 2           # numery kolumn listy parametrów
TODO_ROLE = Qt.ItemDataRole.UserRole + 1        # „ten parametr albo jego wartości mają coś do tłumaczenia”


def value_columns() -> list[Column]:
    """Kolumny wartości: tylko nazwy, tak samo edytowalne jak w trybie 1."""
    return [
        Column(labels.VALUE_PL, lambda e: e.name_pl),
        Column(labels.CURRENT_EN, lambda e: e.name_en,
               color=lambda e: STATE_COLORS[e.state] if e.needs_translation else None),
        Column(labels.NEW_EN),
    ]


class _ParameterList(QTableWidget):
    """Lista parametrów, w której Enter zapisuje tłumaczenie i otwiera edycję w następnym widocznym wierszu."""

    def closeEditor(self, editor, hint):
        super().closeEditor(editor, hint)
        if hint == QAbstractItemDelegate.EndEditHint.SubmitModelCache:   # zamknięcie Enterem
            row = self.currentRow() + 1
            while row < self.rowCount() and self.isRowHidden(row):
                row += 1
            if row < self.rowCount():
                self.setCurrentCell(row, TRANSLATION)
                self.editItem(self.item(row, TRANSLATION))


class ParameterValuesTab(QWidget):
    def __init__(self, drafts: dict[int, str], on_draft_changed: Callable[[], None], parent=None):
        super().__init__(parent)
        self._drafts = drafts
        self._on_draft_changed = on_draft_changed
        self._dictionary: ParameterDictionary | None = None
        self._current: DictionaryElement | None = None
        self._refresh_pending = False

        # --- lewa strona: parametry (nazwa PL, EN w sklepie, tłumaczenie do wpisania) ---
        self._list_search = QLineEdit(self)
        self._list_search.setPlaceholderText("Szukaj parametru…")
        self._list_search.setClearButtonEnabled(True)
        self._only_todo_check = QCheckBox("Tylko z czymś do tłumaczenia", self)
        self._list = _ParameterList(0, len(LIST_HEADERS), self)
        self._list.setHorizontalHeaderLabels(LIST_HEADERS)
        self._list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed
                                   | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self._list.verticalHeader().setVisible(False)
        self._list.setWordWrap(False)
        self._list.setAlternatingRowColors(True)
        self._list.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._list.horizontalHeader().setStretchLastSection(True)
        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self._list_search)
        left_layout.addWidget(self._only_todo_check)
        left_layout.addWidget(self._list)

        # --- prawa strona: wartości wybranego parametru ---
        self._title = QLabel(self)
        self._title.setTextFormat(Qt.TextFormat.RichText)
        self.values = DictionaryTab(drafts, on_draft_changed, with_parameter_filter=True, excel_what="wartości",
                                    export_all_label="Zgraj wszystkie parametry", show_parameter_combo=False)
        self.excel_bar = self.values.excel_bar
        self.values._search_edit.setPlaceholderText("Szukaj wartości (PL / EN / tłumaczenie)…")
        right = QWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self._title)
        right_layout.addWidget(self.values)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([700, 800])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self._filter_list)
        self._list_search.textChanged.connect(lambda: self._search_timer.start())
        self._only_todo_check.toggled.connect(lambda: self._filter_list())
        self._list.currentCellChanged.connect(lambda *_: self._on_parameter_selected())
        self._list.itemChanged.connect(self._on_list_item_changed)
        self._show_title()

    # ================= dane =================
    def set_content(self, dictionary: ParameterDictionary, products: ProductIndex | None = None) -> None:
        """products nie jest tu używane (towary są w trybie 3) - zostaje dla zgodności z oknem."""
        self._dictionary = dictionary
        selected_id = self._current.id if self._current else ALL_PARAMETERS_ID
        values = natural_sorted(natural_sorted(dictionary.values, lambda v: v.name_pl), dictionary.parent_name)
        choices = [(p.id, p.name_pl) for p in dictionary.parameters_with_values()]
        self.values.set_content(values, value_columns(), choices)
        self._fill_list()
        self.select(selected_id)

    def _parameters(self) -> list[DictionaryElement]:
        return natural_sorted((p for p in self._dictionary.parameters if p.type == TYPE_PARAMETER),
                              lambda p: p.name_pl)

    def _fill_list(self) -> None:
        self._list.blockSignals(True)       # bez reakcji na itemChanged przy wypełnianiu
        parameters = self._parameters()
        values_todo = {p.id: False for p in parameters}
        for value in self._dictionary.values:
            if value.parameter_id in values_todo and value.needs_translation and value.id not in self._drafts:
                values_todo[value.parameter_id] = True
        self._list.setRowCount(len(parameters) + 1)

        all_item = QTableWidgetItem("(wszystkie parametry)")
        all_item.setData(Qt.ItemDataRole.UserRole, ALL_PARAMETERS_ID)
        self._list.setItem(0, NAME, all_item)
        for column in (ENGLISH, TRANSLATION):
            self._list.setItem(0, column, self._read_only(""))

        bold = QFont()
        bold.setBold(True)
        for row, parameter in enumerate(parameters, start=1):
            own_todo = parameter.needs_translation and parameter.id not in self._drafts
            name_item = self._read_only(parameter.name_pl)
            name_item.setData(Qt.ItemDataRole.UserRole, parameter.id)
            name_item.setData(TODO_ROLE, own_todo or values_todo[parameter.id])
            english_item = self._read_only(parameter.name_en)
            if parameter.needs_translation:
                english_item.setForeground(RED)
            translation_item = QTableWidgetItem(self._drafts.get(parameter.id, ""))   # edytowalna komórka
            translation_item.setFont(bold)
            translation_item.setToolTip(TRANSLATION_TOOLTIP)
            self._list.setItem(row, NAME, name_item)
            self._list.setItem(row, ENGLISH, english_item)
            self._list.setItem(row, TRANSLATION, translation_item)
        self._list.resizeColumnsToContents()
        self._list.setColumnWidth(NAME, min(self._list.columnWidth(NAME), 260))
        self._list.setColumnWidth(ENGLISH, min(self._list.columnWidth(ENGLISH), 260))
        self._list.blockSignals(False)
        self._filter_list()

    @staticmethod
    def _read_only(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    def _filter_list(self) -> None:
        text = self._list_search.text().strip().lower()
        only_todo = self._only_todo_check.isChecked()
        for row in range(1, self._list.rowCount()):
            names = " ".join(self._list.item(row, c).text() for c in (NAME, ENGLISH, TRANSLATION)).lower()
            hidden = (text and text not in names) or (only_todo and not self._list.item(row, NAME).data(TODO_ROLE))
            self._list.setRowHidden(row, bool(hidden))

    # ================= edycja tłumaczenia parametru =================
    def _on_list_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != TRANSLATION or self._dictionary is None:
            return
        parameter_id = self._list.item(item.row(), NAME).data(Qt.ItemDataRole.UserRole)
        if parameter_id == ALL_PARAMETERS_ID:
            return
        text = item.text().strip()
        if text == self._drafts.get(parameter_id, ""):
            return
        if text:
            self._drafts[parameter_id] = text
        else:
            self._drafts.pop(parameter_id, None)    # puste pole = bez zmian
        self._on_draft_changed()

    # ================= wybór parametru =================
    def select(self, parameter_id: int) -> None:
        for row in range(self._list.rowCount()):
            if self._list.item(row, NAME).data(Qt.ItemDataRole.UserRole) == parameter_id:
                self._list.setRowHidden(row, False)
                self._list.setCurrentCell(row, NAME)
                self._list.scrollToItem(self._list.item(row, NAME))
                self._on_parameter_selected()
                return

    def reveal(self, element_id: int) -> None:
        """Pokazuje parametr albo wartość (np. po dwukliku w trybie 1 lub 3)."""
        if self._dictionary is None or element_id not in self._dictionary.elements:
            return
        element = self._dictionary.elements[element_id]
        self.select(element.parameter_id if element.parameter_id else element.id)
        if element.parameter_id:
            for row in range(self.values.proxy.rowCount()):
                index = self.values.proxy.index(row, 0)
                if self.values.element_at(index).id == element_id:
                    self.values.table.setCurrentIndex(index)
                    self.values.table.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
                    break

    def _on_parameter_selected(self) -> None:
        row = self._list.currentRow()
        if self._dictionary is None or row < 0:
            return
        parameter_id = self._list.item(row, NAME).data(Qt.ItemDataRole.UserRole)
        self._current = self._dictionary.elements.get(parameter_id)
        self.values.select_parameter_id(self._current.id if self._current else None)
        self._show_title()

    def _show_title(self) -> None:
        if self._current is None:
            self._title.setText("<b>Wartości wszystkich parametrów</b> – wybierz parametr z listy po lewej")
        else:
            self._title.setText(f"<b>Wartości parametru „{self._current.name_pl}”</b>")

    def refresh_after_draft_change(self) -> None:
        """Po zmianie tłumaczeń (np. w trybie 1 albo z Excela) - odświeża listę parametrów.
        Z opóźnieniem 0 ms: lista nie może się przebudować w trakcie obsługi własnej edycji."""
        if not self._refresh_pending:
            self._refresh_pending = True
            QTimer.singleShot(0, self._refresh_now)

    def _refresh_now(self) -> None:
        self._refresh_pending = False
        if self._dictionary is None or self._list.state() == QAbstractItemView.State.EditingState:
            self.values.table.viewport().update()
            return
        current = self._current.id if self._current else ALL_PARAMETERS_ID
        self._fill_list()
        self._list.blockSignals(True)
        for row in range(self._list.rowCount()):
            if self._list.item(row, NAME).data(Qt.ItemDataRole.UserRole) == current:
                self._list.setCurrentCell(row, self._list.currentColumn() if self._list.currentColumn() >= 0 else NAME)
        self._list.blockSignals(False)
        self.values.table.viewport().update()

    # ================= dla okna =================
    def apply_filters(self) -> None:
        self.values.apply_filters()

    def visible_values(self) -> list[DictionaryElement]:
        """Wartości widoczne w tabeli (wybrany parametr + filtry)."""
        return self.values.visible_elements()

    def all_filtered_values(self) -> list[DictionaryElement]:
        """Wartości WSZYSTKICH parametrów z bieżącymi filtrami (stan, szukaj)."""
        previous = self._current.id if self._current else None
        self.values.select_parameter_id(None)
        values = self.values.visible_elements()
        self.values.select_parameter_id(previous)
        return values
