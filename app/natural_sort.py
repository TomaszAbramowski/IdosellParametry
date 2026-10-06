"""Sortowanie „naturalne”: liczby w tekście porównujemy jak liczby, litery według polskiego alfabetu.

Zwykłe sortowanie tekstu daje: 130, 14, 15, 150, 16, 2   (znak po znaku)
Naturalne daje:                2, 14, 15, 16, 130, 150
Działa też dla „5-cio strzałowy” < „6-cio strzałowy” i „200 bar” < „1000 bar”.
"""
import re
from functools import cmp_to_key
from typing import Callable, Iterable, TypeVar

from PySide6.QtCore import QCollator, QLocale, QSortFilterProxyModel, Qt

NUMBER = re.compile(r"(\d+(?:[.,]\d+)?)")
SORT_ROLE = Qt.ItemDataRole.UserRole   # osobna „rola” z wartością do sortowania (liczba albo tekst)

_collator = QCollator(QLocale(QLocale.Language.Polish, QLocale.Country.Poland))   # „ś” obok „s”, nie za „z”
_collator.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)


def _split(text: str) -> list[float | str]:
    """„6-cio strzałowy” -> [6.0, "-cio strzałowy"]"""
    parts = []
    for part in NUMBER.split(text.strip()):
        if not part:
            continue
        parts.append(float(part.replace(",", ".")) if NUMBER.fullmatch(part) else part)
    return parts


def natural_compare(first: str, second: str) -> int:
    """Ujemne gdy first < second, 0 gdy równe, dodatnie gdy first > second (jak compareTo w Javie)."""
    for a, b in zip(_split(first), _split(second)):
        a_is_number, b_is_number = isinstance(a, float), isinstance(b, float)
        if a_is_number and b_is_number:
            if a != b:
                return -1 if a < b else 1
        elif a_is_number or b_is_number:
            return -1 if a_is_number else 1          # liczby przed tekstem
        else:
            result = _collator.compare(a, b)
            if result:
                return result
    return len(_split(first)) - len(_split(second))


T = TypeVar("T")


def natural_sorted(items: Iterable[T], name: Callable[[T], str]) -> list[T]:
    """sorted() z porównaniem naturalnym, np. natural_sorted(produkty, lambda p: p.name)."""
    compare = cmp_to_key(natural_compare)
    return sorted(items, key=lambda item: compare(name(item)))


class NaturalSortProxy(QSortFilterProxyModel):
    """Nakładka sortująca naturalnie. Filtry dopisują klasy dziedziczące (jak extends w Javie)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortRole(SORT_ROLE)
        # Bez automatycznego filtrowania po edycji: wiersz z nowym tłumaczeniem nie znika spod kursora.
        self.setDynamicSortFilter(False)

    def lessThan(self, left, right) -> bool:
        a, b = left.data(SORT_ROLE), right.data(SORT_ROLE)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a < b
        return natural_compare(str(a or ""), str(b or "")) < 0

    def refilter(self, change) -> None:
        """Zmienia ustawienia filtra i przelicza widoczne wiersze.
        Nowe Qt (6.10+): begin/endFilterChange; starsze: invalidateFilter (w nowych - przestarzałe)."""
        new_api = hasattr(self, "endFilterChange")
        if new_api:
            self.beginFilterChange()
        change()
        if new_api:
            self.endFilterChange()
        else:
            self.invalidateFilter()
