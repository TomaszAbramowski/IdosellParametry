"""Panel z produktami w grupach (drzewo): grupa -> produkty. Tylko podgląd - nic tu nie zmienia danych.

Grupami mogą być kategorie („Noże i akcesoria (7)”) albo wartości parametru („liner lock (21)”).
"""
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from app.products import Product

HEADERS = ["Grupa / produkt", "Kategoria", "Kod", "ID"]
NO_CATEGORY = "(bez kategorii)"
AUTO_EXPAND_LIMIT = 300        # przy większej liczbie produktów grupy zostają zwinięte
MAX_NAME_WIDTH = 650

Group = tuple[str, list[Product]]   # (nazwa grupy, produkty w grupie)


def group_by_category(products: list[Product]) -> list[Group]:
    groups: dict[str, list[Product]] = {}
    for product in products:
        groups.setdefault(product.category or NO_CATEGORY, []).append(product)
    return [(category, groups[category]) for category in sorted(groups, key=str.lower)]


class ProductsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._title = QLabel(self)
        self._title.setWordWrap(True)
        self._tree = QTreeWidget(self)
        self._tree.setHeaderLabels(HEADERS)
        self._tree.setAlternatingRowColors(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._title)
        layout.addWidget(self._tree)

    def show_message(self, text: str) -> None:
        self._title.setText(text)
        self._tree.clear()

    def show_groups(self, heading: str, groups: list[Group]) -> None:
        """heading może zawierać proste HTML (np. <b>)."""
        self._title.setText(heading)
        self._tree.clear()
        bold = QFont()
        bold.setBold(True)
        total = 0
        for label, products in groups:
            group_item = QTreeWidgetItem([f"{label}  ({len(products)})"])
            group_item.setFont(0, bold)
            for product in products:
                group_item.addChild(QTreeWidgetItem([product.name, product.category, product.code, str(product.id)]))
            self._tree.addTopLevelItem(group_item)
            total += len(products)
        if total <= AUTO_EXPAND_LIMIT:
            self._tree.expandAll()
        for column in range(len(HEADERS)):
            self._tree.resizeColumnToContents(column)
        self._tree.setColumnWidth(0, min(self._tree.columnWidth(0), MAX_NAME_WIDTH))
