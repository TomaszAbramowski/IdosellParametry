"""Tryb 3: lista produktów, a pod spodem parametry zaznaczonego produktu (albo kilku - co mają wspólne,
a czym się różnią). TYLKO PODGLĄD - na tym etapie nic tu nie zmienia produktów w sklepie.
"""
from collections import Counter
from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QSplitter, QTableView, QVBoxLayout, QWidget)

from app.dictionary import TYPE_PARAMETER, DictionaryElement, ParameterDictionary
from app.dictionary_tab import ALL_CATEGORIES, SEARCH_DELAY_MS, create_searchable_combo, fill_combo
from app.natural_sort import SORT_ROLE, NaturalSortProxy, natural_sorted
from app.products import Product, ProductIndex

MAX_COLUMN_WIDTH = 500
RED, ORANGE, GREEN = QColor("#c62828"), QColor("#e65100"), QColor("#2e7d32")


# ======================= lista produktów =======================

@dataclass(frozen=True)
class ProductStats:
    parameters: int           # ile parametrów ma produkt
    untranslated: int         # ile jego parametrów/wartości nie ma tłumaczenia EN
    polish_gaps: int          # ile ma zaślepek po polsku („dane do uzupełnienia”, puste)


def product_stats(product: Product, dictionary: ParameterDictionary | None) -> ProductStats:
    if dictionary is None:
        return ProductStats(len(product.parameter_ids), 0, 0)
    parameters = [dictionary.elements[i] for i in product.parameter_ids
                  if i in dictionary.elements and dictionary.elements[i].type == TYPE_PARAMETER]
    values = [dictionary.elements[i] for i in product.value_ids if i in dictionary.elements]
    return ProductStats(len(parameters),
                        sum(e.needs_translation for e in parameters + values),
                        sum(v.has_polish_problem for v in values))


PRODUCT_HEADERS = ["Nazwa produktu", "Kod", "Kategoria", "Parametry", "Do tłum. EN", "Braki PL", "ID"]


class ProductListModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._products: list[Product] = []
        self._stats: dict[int, ProductStats] = {}

    def set_content(self, products: list[Product], dictionary: ParameterDictionary | None) -> None:
        self.beginResetModel()
        self._products = products
        self._stats = {p.id: product_stats(p, dictionary) for p in products}
        self.endResetModel()

    def product_at(self, row: int) -> Product:
        return self._products[row]

    def stats_of(self, product: Product) -> ProductStats:
        return self._stats[product.id]

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._products)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(PRODUCT_HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return PRODUCT_HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        product = self._products[index.row()]
        stats = self._stats[product.id]
        values = (product.name, product.code, product.category, stats.parameters, stats.untranslated,
                  stats.polish_gaps, product.id)
        value = values[index.column()]
        if role in (Qt.ItemDataRole.DisplayRole, SORT_ROLE):
            return value
        if role == Qt.ItemDataRole.ForegroundRole and index.column() in (4, 5) and value:
            return RED
        return None


PRODUCT_FILTERS = ["Wszystkie produkty", "Z nieprzetłumaczonymi parametrami / wartościami",
                   "Z brakami po polsku (dane do uzupełnienia)"]


class ProductFilterProxy(NaturalSortProxy):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._text, self._category, self._mode = "", None, 0

    def set_filters(self, text: str, category: str | None, mode: int) -> None:
        def change():
            self._text, self._category, self._mode = text.strip().lower(), category, mode
        self.refilter(change)

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        model: ProductListModel = self.sourceModel()
        product = model.product_at(source_row)
        stats = model.stats_of(product)
        if self._category and product.category != self._category:
            return False
        if self._mode == 1 and not stats.untranslated:
            return False
        if self._mode == 2 and not stats.polish_gaps:
            return False
        if self._text:
            return self._text in f"{product.name} {product.code} {product.id}".lower()
        return True


# ======================= parametry zaznaczonych produktów =======================

@dataclass(frozen=True)
class ParameterSummary:
    """Jeden parametr w zaznaczonych produktach: jakie ma wartości i u ilu produktów."""
    parameter: DictionaryElement
    values: list[tuple[DictionaryElement, int]]   # (wartość, u ilu produktów), najczęstsze pierwsze
    product_count: int                            # ilu zaznaczonych produktów ma ten parametr


def summarize_parameters(products: list[Product], dictionary: ParameterDictionary) -> list[ParameterSummary]:
    has_parameter: Counter = Counter()
    value_counts: dict[int, Counter] = {}
    for product in products:
        for parameter_id in product.parameter_ids:
            has_parameter[parameter_id] += 1
        for value_id in product.value_ids:
            value = dictionary.elements.get(value_id)
            if value is not None:
                value_counts.setdefault(value.parameter_id, Counter())[value_id] += 1
    summaries = []
    for parameter_id in set(has_parameter) | set(value_counts):
        parameter = dictionary.elements.get(parameter_id)
        if parameter is None or parameter.type != TYPE_PARAMETER:
            continue                                  # sekcje i nieznane ID pomijamy
        values = [(dictionary.elements[v], n) for v, n in (value_counts.get(parameter_id) or Counter()).most_common()]
        summaries.append(ParameterSummary(parameter, values, has_parameter[parameter_id] or
                                          max((n for _, n in values), default=0)))
    return natural_sorted(summaries, lambda s: s.parameter.name_pl)


DETAIL_HEADERS = ["Parametr (PL)", "Wartość (PL)", "Parametr (EN)", "Wartość (EN)", "Stan", "Produkty"]


class ParameterDetailsModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[ParameterSummary] = []
        self._selected_count = 0

    def set_content(self, rows: list[ParameterSummary], selected_count: int) -> None:
        self.beginResetModel()
        self._rows, self._selected_count = rows, selected_count
        self.endResetModel()

    def summary_at(self, row: int) -> ParameterSummary:
        return self._rows[row]

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(DETAIL_HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return DETAIL_HEADERS[section]
        return None

    @staticmethod
    def _values_text(summary: ParameterSummary, english: bool) -> str:
        if not summary.values:
            return "—"
        names = [(v.name_en or "∅") if english else v.name_pl for v, _ in summary.values]
        if len(names) == 1:
            return names[0]
        return "  |  ".join(f"{name} ({count})" for name, (_, count) in zip(names, summary.values))

    @staticmethod
    def _state(summary: ParameterSummary) -> tuple[str, QColor | None]:
        values = [v for v, _ in summary.values]
        if any(v.has_polish_problem for v in values):
            return "Braki PL", RED
        if summary.parameter.needs_translation or any(v.needs_translation for v in values):
            return "Do tłumaczenia", ORANGE
        return "OK", GREEN

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        summary = self._rows[index.row()]
        column = index.column()
        state_text, state_color = self._state(summary)
        values = (summary.parameter.name_pl, self._values_text(summary, False), summary.parameter.name_en,
                  self._values_text(summary, True), state_text,
                  f"{summary.product_count} z {self._selected_count}")
        if role == Qt.ItemDataRole.DisplayRole:
            return values[column]
        if role == SORT_ROLE:
            return summary.product_count if column == 5 else values[column]
        if role == Qt.ItemDataRole.ForegroundRole:
            if column == 4:
                return state_color
            if column == 2 and summary.parameter.needs_translation:
                return RED
            if column == 3 and any(v.needs_translation for v, _ in summary.values):
                return RED
        if role == Qt.ItemDataRole.ToolTipRole and column in (1, 3) and len(summary.values) > 1:
            return "Zaznaczone produkty mają różne wartości (w nawiasie – u ilu produktów)"
        return None


# ======================= zakładka =======================

class ProductsTab(QWidget):
    reveal_requested = Signal(int)   # „pokaż ten parametr / wartość w drzewie” (ID elementu)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dictionary: ParameterDictionary | None = None
        self._products: ProductIndex | None = None

        self._list_model = ProductListModel(self)
        self._list_proxy = ProductFilterProxy(self)
        self._list_proxy.setSourceModel(self._list_model)
        self._details_model = ParameterDetailsModel(self)
        self._details_proxy = NaturalSortProxy(self)
        self._details_proxy.setSourceModel(self._details_model)

        self._search_edit = QLineEdit(self)
        self._search_edit.setPlaceholderText("Nazwa, kod albo ID produktu…")
        self._search_edit.setClearButtonEnabled(True)
        self._category_combo = create_searchable_combo(self, 220)
        self._mode_combo = QComboBox(self)
        self._mode_combo.addItems(PRODUCT_FILTERS)
        clear_button = QPushButton("Wyczyść filtry", self)
        self._count_label = QLabel(self)
        filters = QHBoxLayout()
        for widget, stretch in ((QLabel("Szukaj:"), 0), (self._search_edit, 2), (QLabel("Kategoria:"), 0),
                                (self._category_combo, 2), (QLabel("Pokaż:"), 0), (self._mode_combo, 0),
                                (clear_button, 0), (self._count_label, 0)):
            filters.addWidget(widget, stretch=stretch)

        self.products_table = self._create_table(self._list_proxy, QAbstractItemView.SelectionMode.ExtendedSelection)
        self._details_title = QLabel(self)
        self._details_title.setWordWrap(True)
        self.details_table = self._create_table(self._details_proxy, QAbstractItemView.SelectionMode.SingleSelection)
        details = QWidget(self)
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.addWidget(self._details_title)
        details_layout.addWidget(self.details_table)

        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.addWidget(self.products_table)
        splitter.addWidget(details)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(filters)
        layout.addWidget(splitter)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self.apply_filters)
        self._search_edit.textChanged.connect(lambda: self._search_timer.start())
        self._category_combo.currentIndexChanged.connect(lambda: self.apply_filters())
        self._mode_combo.currentIndexChanged.connect(lambda: self.apply_filters())
        clear_button.clicked.connect(self.clear_filters)
        self.products_table.selectionModel().selectionChanged.connect(lambda *_: self._show_details())
        self.details_table.doubleClicked.connect(self._reveal_parameter)
        self._show_details()

    @staticmethod
    def _create_table(model, selection_mode) -> QTableView:
        table = QTableView()
        table.setModel(model)
        table.setSortingEnabled(True)
        table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(selection_mode)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)   # tylko podgląd
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.verticalHeader().setVisible(False)
        return table

    # --- dane ---
    def set_content(self, dictionary: ParameterDictionary | None, products: ProductIndex | None) -> None:
        self._dictionary, self._products = dictionary, products
        all_products = natural_sorted(products.products, lambda p: p.name) if products else []
        self._list_model.set_content(all_products, dictionary)
        fill_combo(self._category_combo, "Wszystkie kategorie", ALL_CATEGORIES,
                   [(c, c) for c in products.categories] if products else [])
        self.apply_filters()
        _fit(self.products_table)
        self._show_details()

    # --- filtry ---
    def apply_filters(self) -> None:
        self._list_proxy.set_filters(self._search_edit.text(), self._category_combo.currentData() or None,
                                     self._mode_combo.currentIndex())
        self._count_label.setText(f"Widoczne: {self._list_proxy.rowCount()} z {self._list_model.rowCount()}")

    def clear_filters(self) -> None:
        for widget in (self._search_edit, self._category_combo, self._mode_combo):
            widget.blockSignals(True)
        self._search_edit.clear()
        self._category_combo.setCurrentIndex(0)
        self._mode_combo.setCurrentIndex(0)
        for widget in (self._search_edit, self._category_combo, self._mode_combo):
            widget.blockSignals(False)
        self.apply_filters()

    # --- szczegóły ---
    def _selected_products(self) -> list[Product]:
        rows = self.products_table.selectionModel().selectedRows()
        return [self._list_model.product_at(self._list_proxy.mapToSource(index).row()) for index in rows]

    def _show_details(self) -> None:
        if self._products is None:
            self._details_title.setText("Kliknij „Pobierz produkty”, żeby zobaczyć listę produktów.")
            self._details_model.set_content([], 0)
            return
        if self._dictionary is None:
            self._details_title.setText("Pobierz też słownik – bez niego nie da się pokazać nazw parametrów.")
            self._details_model.set_content([], 0)
            return
        selected = self._selected_products()
        if not selected:
            self._details_title.setText("Zaznacz produkt, żeby zobaczyć jego parametry. Ctrl + klik zaznacza kilka – "
                                        "wtedy widać, co mają wspólne, a czym się różnią.")
            self._details_model.set_content([], 0)
            return
        summaries = summarize_parameters(selected, self._dictionary)
        if len(selected) == 1:
            product = selected[0]
            title = f"<b>{product.name}</b>  ({product.code}, {product.category or 'bez kategorii'}) – " \
                    f"{len(summaries)} parametrów"
        else:
            different = sum(1 for s in summaries if len(s.values) > 1 or s.product_count < len(selected))
            title = f"<b>{len(selected)} zaznaczonych produktów</b> – {len(summaries)} parametrów, " \
                    f"w tym {different} różniących się (wartość albo obecność parametru)"
        self._details_title.setText(title + ".  Dwuklik na parametrze pokazuje go w drzewie.")
        self._details_model.set_content(summaries, len(selected))
        _fit(self.details_table)

    def _reveal_parameter(self, index: QModelIndex) -> None:
        summary = self._details_model.summary_at(self._details_proxy.mapToSource(index).row())
        target = summary.values[0][0] if len(summary.values) == 1 else summary.parameter
        self.reveal_requested.emit(target.id)


def _fit(table: QTableView) -> None:
    table.resizeColumnsToContents()
    for column in range(table.model().columnCount() - 1):   # ostatnia kolumna wypełnia resztę szerokości
        table.setColumnWidth(column, min(table.columnWidth(column), MAX_COLUMN_WIDTH))
