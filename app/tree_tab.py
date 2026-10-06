"""Tryb 2: drzewo „parametr -> wartości” z filtrami, a pod spodem produkty zaznaczonego elementu.

- zaznaczony PARAMETR  -> jego produkty pogrupowane według wartości („liner lock (21)”, „brak (12)” …)
- zaznaczona WARTOŚĆ   -> jej produkty pogrupowane według kategorii
- kilka wartości (Ctrl) -> produkty, które mają WSZYSTKIE zaznaczone wartości
"""
from typing import Callable

from PySide6.QtCore import QModelIndex, Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemDelegate, QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QSplitter, QTreeView, QVBoxLayout,
                               QWidget)

from app.dictionary import TYPE_PARAMETER, TYPE_VALUE, DictionaryElement, ParameterDictionary
from app.dictionary_tab import ALL_CATEGORIES, SEARCH_DELAY_MS, ExcelBar, create_searchable_combo, fill_combo
from app.natural_sort import natural_sorted
from app.products import ProductIndex
from app.products_panel import ProductsPanel, group_by_category
from app.table_model import STATE_COLORS, STATE_FILTERS, Column
from app.tree_model import ParameterTreeModel, ParameterTreeProxy

MAX_COLUMN_WIDTH = 380
TRANSLATION_COLUMN_WIDTH = 280


def tree_columns(dictionary: ParameterDictionary, products: ProductIndex | None) -> list[Column]:
    """Te same kolumny dla parametrów i wartości - lambda sprawdza, czym jest element."""
    def is_value(e: DictionaryElement) -> bool:
        return e.type == TYPE_VALUE

    def product_count(e: DictionaryElement):
        if products is None:
            return ""
        return len(products.products_with(e.id)) if is_value(e) else products.product_count_with_parameter(e.id)

    def categories_tooltip(e: DictionaryElement) -> str:
        if products is None or not is_value(e):
            return ""
        categories = products.categories_of(e.id)
        return "Kategorie produktów:\n" + "\n".join(categories) if categories else ""

    return [
        Column("Nazwa (PL)", lambda e: e.name_pl),
        Column("Nazwa (EN)", lambda e: e.name_en,
               color=lambda e: STATE_COLORS[e.state] if e.needs_translation else None),
        Column("Stan PL", lambda e: e.polish_state.value, is_polish_state=True),
        Column("Stan EN", lambda e: e.state.value, is_state=True),
        Column("Wartości", lambda e: "" if is_value(e) else dictionary.value_count_of(e.id)),
        Column("Do tłum.", lambda e: "" if is_value(e) else dictionary.untranslated_values_of(e.id)),
        Column("Produkty", product_count, tooltip=categories_tooltip),
        Column("ID", lambda e: e.id),
        Column("Tłumaczenie EN"),
    ]


class TranslationTreeView(QTreeView):
    """Drzewo, w którym Enter zapisuje tłumaczenie i od razu otwiera edycję w wierszu poniżej."""

    def closeEditor(self, editor, hint):
        super().closeEditor(editor, hint)
        if hint == QAbstractItemDelegate.EndEditHint.SubmitModelCache:
            below = self.indexBelow(self.currentIndex())
            if below.isValid():
                below = below.siblingAtColumn(self.currentIndex().column())
                self.setCurrentIndex(below)
                self.edit(below)


class ParameterTreeTab(QWidget):
    def __init__(self, drafts: dict[int, str], on_draft_changed: Callable[[], None], parent=None):
        super().__init__(parent)
        self._dictionary: ParameterDictionary | None = None
        self._products: ProductIndex | None = None
        self.model = ParameterTreeModel(drafts, on_draft_changed, self)
        self.proxy = ParameterTreeProxy(self)
        self.proxy.setSourceModel(self.model)

        self._search_edit = QLineEdit(self)
        self._search_edit.setPlaceholderText("Parametr, wartość, ID, nazwa produktu…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMinimumWidth(220)
        self._state_combo = QComboBox(self)
        for state_filter in STATE_FILTERS:
            self._state_combo.addItem(state_filter.label)
        self._category_combo = create_searchable_combo(self, 220)
        self._only_used_check = QCheckBox("Tylko użyte w produktach", self)
        expand_button = QPushButton("Rozwiń", self)
        collapse_button = QPushButton("Zwiń", self)
        clear_button = QPushButton("Wyczyść filtry", self)
        self._count_label = QLabel(self)

        filters = QHBoxLayout()
        for widget, stretch in ((QLabel("Szukaj:"), 0), (self._search_edit, 2), (QLabel("Stan:"), 0),
                                (self._state_combo, 0), (QLabel("Kategoria:"), 0), (self._category_combo, 2),
                                (self._only_used_check, 0), (expand_button, 0), (collapse_button, 0),
                                (clear_button, 0), (self._count_label, 0)):
            filters.addWidget(widget, stretch=stretch)

        self.tree = self._create_tree()
        self.products_panel = ProductsPanel(self)
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.products_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(filters)
        self.excel_bar = ExcelBar("wartości", self)
        layout.addWidget(self.excel_bar)
        layout.addWidget(splitter)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self.apply_filters)
        self._search_edit.textChanged.connect(lambda: self._search_timer.start())
        self._state_combo.currentIndexChanged.connect(lambda: self.apply_filters())
        self._category_combo.currentIndexChanged.connect(lambda: self.apply_filters())
        self._only_used_check.toggled.connect(lambda: self.apply_filters())
        expand_button.clicked.connect(self.tree.expandAll)
        collapse_button.clicked.connect(self.tree.collapseAll)
        clear_button.clicked.connect(self.clear_filters)
        self.tree.selectionModel().selectionChanged.connect(lambda *_: self._show_products_of_selection())
        self._set_products_available(False)

    def _create_tree(self) -> QTreeView:
        tree = TranslationTreeView(self)
        tree.setModel(self.proxy)
        tree.setSortingEnabled(True)
        tree.header().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)   # na start kolejność alfabetyczna
        tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        tree.header().setStretchLastSection(True)
        tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)   # Ctrl / Shift
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)   # szybsze rysowanie przy tysiącach wierszy
        tree.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                             | QAbstractItemView.EditTrigger.EditKeyPressed
                             | QAbstractItemView.EditTrigger.AnyKeyPressed)
        return tree

    # --- dane ---
    def set_content(self, dictionary: ParameterDictionary, products: ProductIndex | None) -> None:
        self._dictionary, self._products = dictionary, products
        parameters = natural_sorted((p for p in dictionary.parameters if p.type == TYPE_PARAMETER),
                                    lambda p: p.name_pl)
        children: dict[int, list[DictionaryElement]] = {}
        for value in dictionary.values:
            children.setdefault(value.parameter_id, []).append(value)
        for parameter_id, values in children.items():   # wartości po kolei „naturalnie”: 2, 14, 130
            children[parameter_id] = natural_sorted(values, lambda v: v.name_pl)
        extra_search = None
        if products is not None:
            extra_search = lambda e: " ".join(f"{p.name} {p.code}" for p in products.products_with(e.id))
        columns = tree_columns(dictionary, products)
        self.model.set_content(parameters, children, columns, extra_search)
        self.proxy.category_lookup = products.categories_of if products else None
        self.proxy.used_value_ids = products.used_value_ids if products else None
        fill_combo(self._category_combo, "Wszystkie kategorie", ALL_CATEGORIES,
                   [(c, c) for c in products.categories] if products else [])
        self._set_products_available(products is not None)
        self.apply_filters()
        self._fit_columns(columns)
        self._show_products_of_selection()

    def _set_products_available(self, available: bool) -> None:
        for widget in (self._category_combo, self._only_used_check):
            widget.setEnabled(available)
        if not available:
            self._only_used_check.setChecked(False)
            self._category_combo.setItemText(0, "(najpierw pobierz produkty)")

    def _fit_columns(self, columns: list[Column]) -> None:
        self.tree.expandAll()                     # żeby szerokość uwzględniła też wartości
        for number, column in enumerate(columns[:-1]):   # ostatnia kolumna wypełnia resztę szerokości
            self.tree.resizeColumnToContents(number)
            width = TRANSLATION_COLUMN_WIDTH if column.is_translation else min(self.tree.columnWidth(number),
                                                                                 MAX_COLUMN_WIDTH)
            self.tree.setColumnWidth(number, width)
        if not self.proxy.is_filtering():
            self.tree.collapseAll()

    # --- filtry ---
    def apply_filters(self) -> None:
        category = self._category_combo.currentData() or None
        self.proxy.set_filters(self._search_edit.text(), STATE_FILTERS[self._state_combo.currentIndex()],
                               category, self._only_used_check.isChecked())
        # Przy filtrowaniu rozwijamy wszystko, żeby było widać pasujące wartości
        if self.proxy.is_filtering():
            self.tree.expandAll()
        else:
            self.tree.collapseAll()
        self._update_count()

    def clear_filters(self) -> None:
        widgets = (self._search_edit, self._state_combo, self._category_combo, self._only_used_check)
        for widget in widgets:
            widget.blockSignals(True)
        self._search_edit.clear()
        self._state_combo.setCurrentIndex(0)
        self._category_combo.setCurrentIndex(0)
        self._only_used_check.setChecked(False)
        for widget in widgets:
            widget.blockSignals(False)
        self.apply_filters()

    def _update_count(self) -> None:
        parameters = self.proxy.rowCount()
        values = sum(self.proxy.rowCount(self.proxy.index(row, 0)) for row in range(parameters))
        self._count_label.setText(f"Parametry: {parameters}, wartości: {values}")

    def reveal(self, element_id: int) -> None:
        """Pokazuje i zaznacza parametr albo wartość (np. po dwukliku w innej zakładce)."""
        source = self.model.index_of(element_id)
        if not source.isValid():
            return
        proxy_index = self.proxy.mapFromSource(source)
        if not proxy_index.isValid():          # ukryty przez filtry -> czyścimy je
            self.clear_filters()
            proxy_index = self.proxy.mapFromSource(source)
        if proxy_index.parent().isValid():
            self.tree.expand(proxy_index.parent())
        else:
            self.tree.expand(proxy_index)
        self.tree.setCurrentIndex(proxy_index)
        self.tree.scrollTo(proxy_index, QAbstractItemView.ScrollHint.PositionAtCenter)

    def visible_values(self) -> list[DictionaryElement]:
        """Wartości widoczne w drzewie (po filtrach i sortowaniu), parametr po parametrze."""
        values = []
        for row in range(self.proxy.rowCount()):
            parent = self.proxy.index(row, 0)
            for child_row in range(self.proxy.rowCount(parent)):
                values.append(self.model.element_at(self.proxy.mapToSource(self.proxy.index(child_row, 0, parent))))
        return values

    # --- produkty zaznaczonego elementu ---
    def _selected_elements(self) -> list[DictionaryElement]:
        rows = self.tree.selectionModel().selectedRows()
        return [self.model.element_at(self.proxy.mapToSource(index)) for index in rows]

    def _show_products_of_selection(self) -> None:
        panel = self.products_panel
        if self._products is None:
            panel.show_message("Kliknij „Pobierz produkty”, aby zobaczyć, w których produktach są parametry i wartości.")
            return
        selected = self._selected_elements()
        values = [e for e in selected if e.type == TYPE_VALUE]
        if len(selected) == 1 and selected[0].type != TYPE_VALUE:
            self._show_parameter(selected[0])
        elif values:
            self._show_values(values)
        else:
            panel.show_message("Zaznacz parametr (produkty pogrupowane według wartości) albo wartość "
                               "(produkty pogrupowane według kategorii). Ctrl + klik zaznacza kilka wartości.")

    def _show_parameter(self, parameter: DictionaryElement) -> None:
        groups = []
        for value in self.model.children_of(parameter.id):
            products = self._products.products_with(value.id)
            if products:
                groups.append((f"{value.name_pl}   →   EN: {value.name_en or '—'}", products))
        groups.sort(key=lambda group: -len(group[1]))   # najczęstsze wartości na górze
        total = self._products.product_count_with_parameter(parameter.id)
        self.products_panel.show_groups(
            f"<b>Parametr „{parameter.name_pl}”</b>: {total} produktów, {len(groups)} wartości w użyciu "
            f"(z {len(self.model.children_of(parameter.id))} w słowniku)", groups)

    def _show_values(self, values: list[DictionaryElement]) -> None:
        products = self._products.products_with_all([v.id for v in values])
        labels = [f"{self._dictionary.parent_name(v)}: „{v.name_pl}”" for v in values]
        if len(values) == 1:
            heading = f"<b>Wartość {labels[0]}</b>"
        else:
            heading = f"<b>Produkty, które mają WSZYSTKIE {len(values)} wartości</b> ({'  +  '.join(labels)})"
        if not products:
            self.products_panel.show_message(f"{heading}: brak takich produktów wśród pobranych")
            return
        groups = group_by_category(products)
        self.products_panel.show_groups(f"{heading}: {len(products)} produktów w {len(groups)} kategoriach", groups)

