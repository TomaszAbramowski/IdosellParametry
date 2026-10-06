"""Jedna zakładka: pasek filtrów + tabela. Używana dwa razy - dla parametrów i dla wartości."""
from typing import Callable

from PySide6.QtCore import QModelIndex, Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemDelegate, QAbstractItemView, QCheckBox, QComboBox, QCompleter, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QTableView, QVBoxLayout, QWidget)

from app.dictionary import DictionaryElement
from app.table_model import STATE_FILTERS, Column, DictionaryFilterProxy, DictionaryTableModel

ALL_PARAMETERS = -1
ALL_CATEGORIES = ""
SEARCH_DELAY_MS = 250          # filtrujemy chwilę po ostatnim naciśnięciu klawisza, nie przy każdej literze
MAX_COLUMN_WIDTH = 420
TRANSLATION_COLUMN_WIDTH = 300


class TranslationTableView(QTableView):
    """Tabela, w której Enter zapisuje tłumaczenie i od razu otwiera edycję w wierszu poniżej."""

    def closeEditor(self, editor, hint):
        super().closeEditor(editor, hint)
        if hint == QAbstractItemDelegate.EndEditHint.SubmitModelCache:   # tak Qt zgłasza zamknięcie Enterem
            below = self.currentIndex().siblingAtRow(self.currentIndex().row() + 1)
            if below.isValid():
                self.setCurrentIndex(below)
                self.edit(below)


def create_searchable_combo(parent: QWidget, minimum_width: int) -> QComboBox:
    """Lista rozwijana, w którą można wpisać fragment nazwy - podpowiada pasujące pozycje."""
    combo = QComboBox(parent)
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    combo.setMinimumWidth(minimum_width)
    completer = combo.completer()
    completer.setFilterMode(Qt.MatchFlag.MatchContains)   # „ręko” znajdzie „Materiał rękojeści”
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
    return combo


def fill_combo(combo: QComboBox, first_label: str, first_data, choices: list[tuple[object, str]]) -> None:
    """Wypełnia listę od nowa i - jeśli się da - zostawia poprzedni wybór."""
    selected = combo.currentData()
    combo.blockSignals(True)          # bez filtrowania przy każdym dodanym elemencie
    combo.clear()
    combo.addItem(first_label, first_data)
    for data, label in choices:
        combo.addItem(label, data)
    index = combo.findData(selected) if selected is not None else 0
    combo.setCurrentIndex(max(index, 0))
    combo.blockSignals(False)


class ExcelBar(QFrame):
    """Pasek „Tłumaczenie w Excelu: [Zgraj do Excela] [Wgraj z Excela]” - osobny w każdym trybie."""

    def __init__(self, what: str, parent=None, export_all_label: str | None = None):
        super().__init__(parent)
        self.export_button = QPushButton("Zgraj do Excela", self)
        # opcjonalny drugi przycisk, np. „Zgraj wszystkie parametry” w trybie 2
        self.export_all_button = QPushButton(export_all_label, self) if export_all_label else None
        self.export_button.setToolTip(f"Zapisuje {what} WIDOCZNE w tabeli (z filtrami) do pliku. "
                                      f"Nowe nazwy EN wpisujesz w żółtej kolumnie.")
        self.import_button = QPushButton("Wgraj z Excela", self)
        self.import_button.setToolTip("Wczytuje żółtą kolumnę z nowym angielskim i pokazuje podgląd TYLKO tego, co się zmieni")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(f"Tłumaczenie w Excelu ({what}):"))
        layout.addWidget(self.export_button)
        if self.export_all_button:
            layout.addWidget(self.export_all_button)
        layout.addWidget(self.import_button)
        hint = QLabel("do pliku trafia to, co widać w tabeli – np. ustaw najpierw Stan: „Do tłumaczenia”")
        hint.setEnabled(False)       # szary tekst podpowiedzi
        layout.addWidget(hint)
        layout.addStretch()


class DictionaryTab(QWidget):
    def __init__(self, drafts: dict[int, str], on_draft_changed: Callable[[], None],
                 with_parameter_filter: bool, with_category_filter: bool = False, parent=None,
                 excel_what: str = "parametry i sekcje", export_all_label: str | None = None,
                 show_parameter_combo: bool = True):
        super().__init__(parent)
        self.model = DictionaryTableModel(drafts, on_draft_changed, self)
        self.proxy = DictionaryFilterProxy(self)
        self.proxy.setSourceModel(self.model)

        self._search_edit = QLineEdit(self)
        self._search_edit.setPlaceholderText("ID, nazwa PL / EN, produkt, tłumaczenie…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMinimumWidth(220)
        self._state_combo = QComboBox(self)
        for state_filter in STATE_FILTERS:
            self._state_combo.addItem(state_filter.label)
        self._parameter_combo = create_searchable_combo(self, 300) if with_parameter_filter else None
        self._category_combo = create_searchable_combo(self, 220) if with_category_filter else None
        self._only_used_check = QCheckBox("Tylko użyte w produktach", self) if with_category_filter else None
        if self._only_used_check is not None:
            self._only_used_check.setToolTip("Ukrywa wartości, których nie ma żaden z pobranych produktów")
        if self._category_combo is not None:
            self._set_category_combo_empty()
        clear_button = QPushButton("Wyczyść filtry", self)
        self._count_label = QLabel(self)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("Szukaj:"))
        filters.addWidget(self._search_edit, stretch=2)
        filters.addWidget(QLabel("Stan:"))
        filters.addWidget(self._state_combo)
        if self._parameter_combo and show_parameter_combo:
            filters.addWidget(QLabel("Parametr:"))
            filters.addWidget(self._parameter_combo, stretch=3)
        if self._category_combo:
            filters.addWidget(QLabel("Kategoria:"))
            filters.addWidget(self._category_combo, stretch=2)
        if self._only_used_check:
            filters.addWidget(self._only_used_check)
        filters.addWidget(clear_button)
        filters.addWidget(self._count_label)

        self.table = self._create_table()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(filters)
        self.excel_bar = ExcelBar(excel_what, self, export_all_label)
        if self._parameter_combo is not None and not show_parameter_combo:
            self._parameter_combo.hide()      # parametr wybiera się wtedy z listy obok tabeli
        layout.addWidget(self.excel_bar)
        layout.addWidget(self.table)

        # Opóźnione filtrowanie przy pisaniu
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self.apply_filters)
        self._search_edit.textChanged.connect(lambda: self._search_timer.start())
        for combo in self._combos():
            combo.currentIndexChanged.connect(lambda: self.apply_filters())
        if self._only_used_check:
            self._only_used_check.toggled.connect(lambda: self.apply_filters())
        clear_button.clicked.connect(self.clear_filters)

    def _combos(self) -> list[QComboBox]:
        return [c for c in (self._state_combo, self._parameter_combo, self._category_combo) if c is not None]

    def _create_table(self) -> QTableView:
        table = TranslationTableView(self)
        table.setModel(self.proxy)
        table.setSortingEnabled(True)
        table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)   # na start kolejność słownika
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setStretchLastSection(True)   # „Tłumaczenie EN” wypełnia resztę szerokości
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Ctrl + klik / Shift + klik zaznacza kilka wierszy
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                              | QAbstractItemView.EditTrigger.EditKeyPressed
                              | QAbstractItemView.EditTrigger.AnyKeyPressed)
        return table

    # --- dane ---
    def set_content(self, elements: list[DictionaryElement], columns: list[Column],
                    parameter_choices: list[tuple[int, str]] | None = None,
                    extra_search_text: Callable[[DictionaryElement], str] | None = None) -> None:
        if self._parameter_combo is not None and parameter_choices is not None:
            fill_combo(self._parameter_combo, "Wszystkie parametry", ALL_PARAMETERS, parameter_choices)
        self.model.set_content(elements, columns, extra_search_text)
        self.apply_filters()
        self._fit_columns(columns)

    def set_categories(self, categories: list[str], lookup: Callable[[int], tuple[str, ...]] | None,
                       used_value_ids: set[int] | None = None) -> None:
        """Lista kategorii do filtra + funkcja „ID wartości -> kategorie jej produktów”."""
        if self._category_combo is None:
            return
        self.proxy.category_lookup = lookup
        self.proxy.used_value_ids = used_value_ids
        self._only_used_check.setEnabled(used_value_ids is not None)
        if used_value_ids is None:
            self._only_used_check.setChecked(False)
        if lookup is None:
            self._set_category_combo_empty()
        else:
            self._category_combo.setEnabled(True)
            fill_combo(self._category_combo, "Wszystkie kategorie", ALL_CATEGORIES,
                       [(category, category) for category in categories])

    def _set_category_combo_empty(self) -> None:
        fill_combo(self._category_combo, "(najpierw pobierz produkty)", ALL_CATEGORIES, [])
        self._category_combo.setEnabled(False)

    def _fit_columns(self, columns: list[Column]) -> None:
        self.table.resizeColumnsToContents()
        for number, column in enumerate(columns[:-1]):   # ostatnia kolumna wypełnia resztę szerokości
            width = TRANSLATION_COLUMN_WIDTH if column.is_translation else min(self.table.columnWidth(number),
                                                                                 MAX_COLUMN_WIDTH)
            self.table.setColumnWidth(number, width)

    # --- filtry ---
    def apply_filters(self) -> None:
        parameter_id = None
        if self._parameter_combo is not None:
            data = self._parameter_combo.currentData()
            parameter_id = None if data in (None, ALL_PARAMETERS) else int(data)
        category = None
        if self._category_combo is not None and self._category_combo.currentData():
            category = str(self._category_combo.currentData())
        only_used = self._only_used_check is not None and self._only_used_check.isChecked()
        self.proxy.set_filters(self._search_edit.text(), STATE_FILTERS[self._state_combo.currentIndex()],
                               parameter_id, category, only_used)
        self.update_count()

    def clear_filters(self) -> None:
        widgets = [self._search_edit, *self._combos()]
        if self._only_used_check:
            widgets.append(self._only_used_check)
            self._only_used_check.blockSignals(True)
            self._only_used_check.setChecked(False)
        for widget in widgets:
            widget.blockSignals(True)
        self._search_edit.clear()
        for combo in self._combos():
            if combo is not self._parameter_combo or not combo.isHidden():   # ukryty wybór parametru zostaje
                combo.setCurrentIndex(0)
        for widget in widgets:
            widget.blockSignals(False)
        self.apply_filters()

    def select_parameter(self, parameter_id: int) -> None:
        if self._parameter_combo is None:
            return
        index = self._parameter_combo.findData(parameter_id)
        if index >= 0:
            self._parameter_combo.setCurrentIndex(index)   # sam wywoła apply_filters

    def select_parameter_id(self, parameter_id: int | None) -> None:
        """Ustawia filtr parametru (None = wszystkie) - także gdy lista wyboru jest ukryta."""
        if self._parameter_combo is None:
            return
        index = 0 if parameter_id is None else self._parameter_combo.findData(parameter_id)
        self._parameter_combo.blockSignals(True)
        self._parameter_combo.setCurrentIndex(max(index, 0))
        self._parameter_combo.blockSignals(False)
        self.apply_filters()

    def update_count(self) -> None:
        self._count_label.setText(f"Widoczne: {self.proxy.rowCount()} z {self.model.rowCount()}")

    # --- pomocnicze dla okna ---
    def element_at(self, proxy_index: QModelIndex) -> DictionaryElement:
        return self.model.element_at(self.proxy.mapToSource(proxy_index).row())

    def visible_elements(self) -> list[DictionaryElement]:
        """Wiersze widoczne w tabeli, w kolejności z ekranu (po filtrach i sortowaniu)."""
        return [self.element_at(self.proxy.index(row, 0)) for row in range(self.proxy.rowCount())]

    def is_translation_column(self, proxy_index: QModelIndex) -> bool:
        return self.model.column(proxy_index.column()).is_translation
