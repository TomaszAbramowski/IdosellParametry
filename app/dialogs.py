"""Okna dialogowe: podgląd zmian przed wysłaniem i raport po wysłaniu."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from app.changes import Change
from app.sender import FAILED, OK, SKIPPED, SendReport

ORANGE, RED, GREEN, GREY = QColor("#e65100"), QColor("#c62828"), QColor("#2e7d32"), QColor("#757575")
STATUS_COLORS = {OK: GREEN, SKIPPED: GREY, FAILED: RED}


def _fill_table(table: QTableWidget, headers: list[str], rows: list[list[str]], colors: list[QColor | None],
                color_columns: tuple[int, ...] = (-1,)) -> None:
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(rows))
    for row_number, (row, color) in enumerate(zip(rows, colors)):
        for column, text in enumerate(row):
            item = QTableWidgetItem(text)
            if color is not None and (column in color_columns or column - len(row) in color_columns):
                item.setForeground(color)
            table.setItem(row_number, column, item)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)
    table.setWordWrap(False)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)   # bez sortowania na start
    table.setSortingEnabled(True)
    table.resizeColumnsToContents()
    for column in range(len(headers) - 1):
        table.setColumnWidth(column, min(table.columnWidth(column), 350))
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)


class ChangesDialog(QDialog):
    """Podgląd: co dokładnie zmieni się w sklepie. Nic nie jest wysyłane bez kliknięcia „Wyślij”."""
    SEND, SAVE_DRAFTS = 10, 11

    def __init__(self, parent, title: str, changes: list[Change], problems: list[str], unchanged: int,
                 test_mode: bool, allow_save_as_drafts: bool):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1300, 700)
        self._changes = changes
        layout = QVBoxLayout(self)

        values = sum(1 for c in changes if c.kind == "wartość")
        summary = (f"<b>Zmian do wysłania: {len(changes)}</b> (parametry i sekcje: {len(changes) - values}, "
                   f"wartości: {values}). Pokazane są TYLKO różnice względem sklepu.")
        if unchanged:
            summary += f"<br>Pominięto {unchanged} wpisów – są takie same jak w sklepie."
        warnings = sum(1 for c in changes if c.note)
        if warnings:
            summary += f"<br><span style='color:#e65100'>Ostrzeżenia: {warnings} – zobacz kolumnę „Uwagi”.</span>"
        label = QLabel(summary, self)
        label.setWordWrap(True)
        layout.addWidget(label)

        if test_mode and changes:
            test_label = QLabel("<b>To będzie PIERWSZY zapis do IdoSella.</b> Jako test program wyśle tylko JEDNĄ "
                                "zmianę (pierwszą z listy), sprawdzi wynik i pokaże raport. Po udanym teście "
                                "pozostałe wyślesz kolejnym kliknięciem „Wyślij tłumaczenia do IdoSella”.", self)
            test_label.setWordWrap(True)
            test_label.setStyleSheet("color: #e65100;")
            layout.addWidget(test_label)

        table = QTableWidget(self)
        rows = [[c.kind, c.parameter, c.name_pl, c.old_en, c.new_en, c.note] for c in changes]
        _fill_table(table, ["Typ", "Parametr", "Po polsku", "Po angielsku – teraz", "Po angielsku – będzie", "Uwagi"], rows,
                    [ORANGE if c.note else None for c in changes])
        layout.addWidget(table)

        if problems:
            layout.addWidget(QLabel(f"Problemy w pliku ({len(problems)}) – te wiersze pominięto:", self))
            text = QPlainTextEdit("\n".join(problems), self)
            text.setReadOnly(True)
            text.setMaximumHeight(110)
            layout.addWidget(text)

        buttons = QHBoxLayout()
        buttons.addStretch()
        count_to_send = 1 if test_mode else len(changes)
        send_button = QPushButton(f"Wyślij do IdoSella ({count_to_send}{' – test' if test_mode else ''})", self)
        send_button.setEnabled(bool(changes))
        send_button.clicked.connect(self._confirm_send)
        buttons.addWidget(send_button)
        if allow_save_as_drafts:
            drafts_button = QPushButton("Zapisz jako robocze (bez wysyłania)", self)
            drafts_button.setEnabled(bool(changes))
            drafts_button.clicked.connect(lambda: self.done(self.SAVE_DRAFTS))
            buttons.addWidget(drafts_button)
        cancel_button = QPushButton("Anuluj", self)
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)
        self._test_mode = test_mode

    def _confirm_send(self) -> None:
        count = 1 if self._test_mode else len(self._changes)
        answer = QMessageBox.question(
            self, "Potwierdź wysyłkę",
            f"Wysłać {count} zmian(y) do IdoSella?\n\nZmiana nazwy w słowniku działa od razu we WSZYSTKICH "
            f"produktach, które używają danego parametru / wartości.\nPrzed zapisem program zrobi kopię.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.done(self.SEND)


class SendResultDialog(QDialog):
    """Raport po wysłaniu: co się udało, co pominięto, co się nie zgadza."""
    RESTORE = 20

    def __init__(self, parent, report: SendReport):
        super().__init__(parent)
        self.setWindowTitle("Wynik wysyłki do IdoSella")
        self.resize(1300, 650)
        layout = QVBoxLayout(self)
        summary = (f"<b>Zapisano i sprawdzono: {report.count(OK)}</b>   |   pominięto: {report.count(SKIPPED)}   |   "
                   f"<span style='color:#c62828'>błędy: {report.count(FAILED)}</span>")
        if report.test_mode and report.count(OK):
            summary += ("<br><b>Test zapisu udany.</b> Kolejne wysyłki pójdą normalnie, paczkami po 20. "
                        "Sprawdź jeszcze ten element w panelu IdoSella i na angielskiej karcie produktu.")
        elif report.test_mode:
            summary += "<br><b>Test zapisu nieudany</b> – kolejne wysyłki nadal będą testem jednego elementu."
        if report.backup_path:
            summary += f"<br>Kopia sprzed zapisu: {report.backup_path}"
        label = QLabel(summary, self)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label)

        table = QTableWidget(self)
        rows = [[o.status, o.change.kind, o.change.parameter, o.change.name_pl, o.change.new_en, o.message]
                for o in report.outcomes]
        _fill_table(table, ["Wynik", "Typ", "Parametr", "Po polsku", "Po angielsku – nowe", "Komunikat"], rows,
                    [STATUS_COLORS.get(o.status) for o in report.outcomes], color_columns=(0, -1))
        layout.addWidget(table)

        buttons = QHBoxLayout()
        buttons.addStretch()
        restorable = [o for o in report.outcomes if o.status == FAILED and o.names_before]
        if restorable and report.last_shape:
            restore_button = QPushButton(f"Przywróć z kopii elementy z błędem ({len(restorable)})", self)
            restore_button.clicked.connect(lambda: self.done(self.RESTORE))
            buttons.addWidget(restore_button)
        close_button = QPushButton("Zamknij", self)
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
