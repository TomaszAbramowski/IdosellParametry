"""Główne okno: pobieranie, trzy tryby (parametry, drzewo, produkty), Excel w obie strony, wysyłka do IdoSella."""
import os
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QModelIndex, Qt, QThread, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QLabel, QMainWindow, QMessageBox,
                               QProgressBar, QTabWidget)

from app import storage
from app.changes import Change, changes_from_drafts, sort_changes
from app.dialogs import ChangesDialog, SendResultDialog
from app.dictionary import LANGUAGES, TYPE_LABELS, ParameterDictionary
from app.dictionary_tab import DictionaryTab
from app.excel_io import (PARAMETERS_SHEET, VALUES_SHEET, WrongFileError, export_parameters, export_values,
                          read_changes_from_excel)
from app.fetch_worker import FetchWorker, Job
from app.idosell_api import IdoSellClient
from app.sender import OK, SendReport, restore_from_backup, send_changes
from app.products import Product, ProductIndex
from app.products_tab import ProductsTab
from app import labels
from app.table_model import STATE_COLORS, Column
from app.values_tab import ParameterValuesTab
from app.natural_sort import natural_sorted

WINDOW_TITLE = "IdoSell Słownik – tłumaczenie parametrów PL → EN"
ALL_PRODUCTS = "Wszystkie"
PRODUCT_LIMIT_CHOICES = ["20", "50", "100", ALL_PRODUCTS]
DEFAULT_PRODUCT_LIMIT = "50"


def parameter_columns(dictionary: ParameterDictionary, products: ProductIndex | None) -> list[Column]:
    columns = [
        Column("ID", lambda e: e.id),
        Column("Typ", lambda e: TYPE_LABELS.get(e.type, e.type)),
        Column(labels.NAME_PL, lambda e: e.name_pl),
        Column(labels.CURRENT_EN, lambda e: e.name_en,
               color=lambda e: STATE_COLORS[e.state] if e.needs_translation else None),
        Column(labels.STATUS, lambda e: e.state.value, is_state=True),
        Column(labels.POLISH_NOTES, lambda e: e.polish_state.value, is_polish_state=True),
        Column("Wartości", lambda e: dictionary.value_count_of(e.id)),
        Column("Wartości do tłum.", lambda e: dictionary.untranslated_values_of(e.id)),
        Column("Wartości z brakami PL", lambda e: dictionary.polish_problem_values_of(e.id)),
    ]
    if products is not None:
        columns.append(Column("Produkty", lambda e: products.product_count_with_parameter(e.id)))
    columns.append(Column(labels.NEW_EN))
    return columns


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self._drafts = self._load_drafts()
        self._dictionary: ParameterDictionary | None = None
        self._dictionary_downloaded_at: datetime | None = None
        self._raw_elements: dict[int, dict] = {}       # surowe dane słownika (do pamięci podręcznej)
        self._send_client: IdoSellClient | None = None
        self._products: ProductIndex | None = None
        self._products_downloaded_at: datetime | None = None
        self._products_limit: int | None = None
        self._pending_products_limit: int | None = None
        self._fetch_thread: QThread | None = None
        self._fetch_worker: FetchWorker | None = None

        self._create_toolbar()
        # Trzy tryby pracy
        self._parameters_tab = DictionaryTab(self._drafts, self._on_draft_changed, with_parameter_filter=False)
        self._tree_tab = ParameterValuesTab(self._drafts, self._on_draft_changed)
        self._products_tab = ProductsTab()
        self._tabs = QTabWidget(self)
        self._tabs.addTab(self._parameters_tab, "1. Parametry")
        self._tabs.addTab(self._tree_tab, "2. Parametry i wartości")
        self._tabs.addTab(self._products_tab, "3. Produkty")
        self.setCentralWidget(self._tabs)
        self._parameters_tab.table.doubleClicked.connect(self._show_parameter_in_tree)
        # Excel - osobno w każdym trybie
        self._parameters_tab.excel_bar.export_button.clicked.connect(lambda: self._export_excel(PARAMETERS_SHEET))
        self._parameters_tab.excel_bar.import_button.clicked.connect(lambda: self._import_excel(PARAMETERS_SHEET))
        self._tree_tab.excel_bar.export_button.clicked.connect(lambda: self._export_excel(VALUES_SHEET))
        self._tree_tab.excel_bar.export_all_button.clicked.connect(lambda: self._export_excel(VALUES_SHEET, True))
        self._tree_tab.excel_bar.import_button.clicked.connect(lambda: self._import_excel(VALUES_SHEET))
        self._products_tab.reveal_requested.connect(self._show_in_tree)

        self._status_label = QLabel(self)
        self._progress_bar = QProgressBar(self)
        self._progress_bar.setMaximumWidth(250)
        self._progress_bar.hide()
        self.statusBar().addWidget(self._status_label, stretch=1)
        self.statusBar().addPermanentWidget(self._progress_bar)

        self._load_from_cache()

    def _create_toolbar(self) -> None:
        toolbar = self.addToolBar("Główne")
        toolbar.setMovable(False)
        self._fetch_dictionary_action = QAction("Pobierz słownik z IdoSella", self)
        self._fetch_dictionary_action.setToolTip("Tylko odczyt – w sklepie nic się nie zmienia")
        self._fetch_dictionary_action.triggered.connect(self._start_dictionary_fetch)
        self._fetch_products_action = QAction("Pobierz produkty (nazwy i kategorie)", self)
        self._fetch_products_action.setToolTip("Tylko odczyt. Potrzebne do kolumn Produkty / Kategorie "
                                               "i filtra kategorii. Może potrwać kilka minut.")
        self._fetch_products_action.triggered.connect(self._start_products_fetch)
        self._product_limit_combo = QComboBox(self)
        self._product_limit_combo.setEditable(True)   # można wpisać własną liczbę, np. 300
        self._product_limit_combo.setMinimumWidth(110)
        self._product_limit_combo.addItems(PRODUCT_LIMIT_CHOICES)
        self._product_limit_combo.setCurrentText(DEFAULT_PRODUCT_LIMIT)
        self._product_limit_combo.setToolTip("Ile produktów pobrać: wybierz z listy albo wpisz liczbę")
        self._cancel_action = QAction("Przerwij pobieranie", self)
        self._cancel_action.setEnabled(False)
        self._cancel_action.triggered.connect(self._cancel_fetch)
        self._send_action = QAction("Wyślij tłumaczenia do IdoSella", self)
        self._send_action.setToolTip("Podgląd tłumaczeń roboczych, które różnią się od sklepu – wysyłka po zatwierdzeniu")
        self._send_action.triggered.connect(self._send_drafts)

        toolbar.addAction(self._fetch_dictionary_action)
        toolbar.addSeparator()
        toolbar.addAction(self._fetch_products_action)
        toolbar.addWidget(QLabel(" Ile: "))
        toolbar.addWidget(self._product_limit_combo)
        toolbar.addSeparator()
        toolbar.addAction(self._cancel_action)
        toolbar.addSeparator()
        toolbar.addAction(self._send_action)

    # --- dane ---
    def _load_drafts(self) -> dict[int, str]:
        try:
            return storage.load_drafts()
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, WINDOW_TITLE, f"Nie udało się wczytać tłumaczeń roboczych:\n{error}\n\n"
                                                     f"Plik: {storage.DRAFTS_FILE}")
            raise SystemExit(1)   # nie pracujemy dalej, żeby nie nadpisać pliku pustymi danymi

    def _load_from_cache(self) -> None:
        problems = []
        try:
            cached_products = storage.load_products_cache()
            if cached_products:
                products, self._products_downloaded_at, self._products_limit = cached_products
                self._products = ProductIndex(products)
        except (OSError, ValueError, KeyError) as error:
            problems.append(f"produktów ({error})")
        try:
            cached_dictionary = storage.load_dictionary_cache()
        except (OSError, ValueError, KeyError) as error:
            problems.append(f"słownika ({error})")
            cached_dictionary = None
        if cached_dictionary is not None:
            self._raw_elements = cached_dictionary[0]
            self._dictionary = ParameterDictionary(cached_dictionary[0])
            self._dictionary_downloaded_at = cached_dictionary[1]
        self._refresh_tabs()
        if self._dictionary is None:
            self._status_label.setText("Brak zapisanego słownika – kliknij „Pobierz słownik z IdoSella”.")
        if problems:
            QMessageBox.warning(self, WINDOW_TITLE, "Nie udało się wczytać zapisanych danych: "
                                + ", ".join(problems) + ".\nPobierz je ponownie.")

    def _refresh_tabs(self) -> None:
        """Odbudowuje wszystkie zakładki z aktualnego słownika i (jeśli są) produktów."""
        self._products_tab.set_content(self._dictionary, self._products)
        if self._dictionary is None:
            return
        self._parameters_tab.set_content(natural_sorted(self._dictionary.parameters, lambda p: p.name_pl),
                                         parameter_columns(self._dictionary, self._products))
        self._tree_tab.set_content(self._dictionary, self._products)
        self._update_status()

    def _update_status(self) -> None:
        if self._dictionary is None:
            return
        if self._products is None:
            products = "produkty: nie pobrane"
        else:
            sample = f" – PRÓBKA (limit {self._products_limit})" if self._products_limit else ""
            products = (f"produkty: {len(self._products.products)}{sample} "
                        f"(z {self._products_downloaded_at:%d.%m %H:%M})")
        self._status_label.setText(
            f"Słownik z {self._dictionary_downloaded_at:%d.%m.%Y %H:%M}   |   parametry i sekcje: "
            f"{len(self._dictionary.parameters)}   |   wartości: {len(self._dictionary.values)}   |   "
            f"{products}   |   moje tłumaczenia: {len(self._drafts)}   |   "
            f"zapis do IdoSella: {'sprawdzony' if storage.load_write_shape() else 'pierwszy będzie testem'}")

    def _on_draft_changed(self) -> None:
        try:
            storage.save_drafts(self._drafts)
        except OSError as error:
            QMessageBox.critical(self, WINDOW_TITLE, f"Nie udało się zapisać tłumaczeń:\n{error}")
        # oba tryby pokazują te same tłumaczenia robocze - odświeżamy je, gdy zmieni się którekolwiek
        self._parameters_tab.table.viewport().update()
        self._tree_tab.refresh_after_draft_change()
        self._update_status()

    # --- akcje ---
    def _show_parameter_in_tree(self, index: QModelIndex) -> None:
        """Dwuklik na parametrze (poza kolumną tłumaczenia) -> ten parametr w drzewie (tryb 2)."""
        if self._parameters_tab.is_translation_column(index):
            return
        self._show_in_tree(self._parameters_tab.element_at(index).id)

    def _show_in_tree(self, element_id: int) -> None:
        self._tabs.setCurrentWidget(self._tree_tab)
        self._tree_tab.reveal(element_id)

    def _export_excel(self, sheet_name: str, all_parameters: bool = False) -> None:
        """Tryb 1 -> plik z parametrami, tryb 2 -> plik z wartościami (to, co widać w zakładce)."""
        if self._dictionary is None:
            QMessageBox.information(self, WINDOW_TITLE, "Najpierw pobierz słownik.")
            return
        if sheet_name == PARAMETERS_SHEET:
            elements = self._parameters_tab.visible_elements()
        else:
            elements = (self._tree_tab.all_filtered_values() if all_parameters
                        else self._tree_tab.visible_values())
        if not elements:
            QMessageBox.information(self, WINDOW_TITLE, "W tabeli nic nie widać – zmień filtry.")
            return
        try:
            if sheet_name == PARAMETERS_SHEET:
                path = export_parameters(self._dictionary, self._drafts, elements)
            else:
                path = export_values(self._dictionary, self._drafts, elements, self._products)
        except OSError as error:   # np. plik otwarty w Excelu
            QMessageBox.critical(self, WINDOW_TITLE, f"Nie udało się zapisać Excela:\n{error}")
            return
        answer = QMessageBox.question(self, WINDOW_TITLE, f"Zapisano {len(elements)} wierszy:\n{path}\n\n"
                                      f"Otworzyć teraz w Excelu?\nNowy angielski tekst wpisuj w żółtej kolumnie „{labels.NEW_EN}”, "
                                      f"zapisz plik i kliknij „Wgraj z Excela” w tej samej zakładce.")
        if answer == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # --- Excel -> podgląd -> IdoSell ---
    def _import_excel(self, sheet_name: str) -> None:
        if self._dictionary is None:
            QMessageBox.information(self, WINDOW_TITLE, "Najpierw pobierz słownik.")
            return
        what = "parametrów" if sheet_name == PARAMETERS_SHEET else "wartości"
        path, _ = QFileDialog.getOpenFileName(self, f"Wybierz plik Excela z tłumaczeniem {what}",
                                              str(storage.DATA_DIRECTORY), "Excel (*.xlsx)")
        if not path:
            return
        try:
            result = read_changes_from_excel(Path(path), self._dictionary, sheet_name)
        except WrongFileError as error:
            QMessageBox.warning(self, WINDOW_TITLE, str(error))
            return
        except Exception as error:   # np. plik otwarty w Excelu albo to nie jest plik .xlsx
            QMessageBox.critical(self, WINDOW_TITLE, f"Nie udało się odczytać pliku:\n{error}")
            return
        if not result.changes:
            details = "\n".join(result.problems[:20])
            QMessageBox.information(self, WINDOW_TITLE, f"Brak różnic względem sklepu – nie ma czego wysłać.\n"
                                                        f"Wpisów takich samych jak w sklepie: {result.unchanged}."
                                                        + (f"\n\n{details}" if details else ""))
            return
        changes = sort_changes(result.changes)
        dialog = ChangesDialog(self, f"Podgląd zmian z Excela – {what}", changes, result.problems, result.unchanged,
                               test_mode=storage.load_write_shape() is None, allow_save_as_drafts=True)
        answer = dialog.exec()
        if answer not in (ChangesDialog.SEND, ChangesDialog.SAVE_DRAFTS):
            return
        # Zmiany z Excela trafiają najpierw do tłumaczeń roboczych - nic nie zginie, nawet gdy wysyłka się nie uda
        try:
            storage.backup_drafts()
        except OSError as error:
            QMessageBox.critical(self, WINDOW_TITLE, f"Nie udało się zrobić kopii tłumaczeń – przerwano:\n{error}")
            return
        for change in changes:
            self._drafts[change.element_id] = change.new_en
        self._on_draft_changed()
        self._parameters_tab.apply_filters()
        self._tree_tab.apply_filters()
        if answer == ChangesDialog.SEND:
            self._start_send(changes)

    def _send_drafts(self) -> None:
        if self._dictionary is None:
            QMessageBox.information(self, WINDOW_TITLE, "Najpierw pobierz słownik.")
            return
        changes = changes_from_drafts(self._drafts, self._dictionary)
        if not changes:
            QMessageBox.information(self, WINDOW_TITLE, "Brak tłumaczeń roboczych, które różnią się od sklepu.")
            return
        dialog = ChangesDialog(self, "Podgląd zmian do wysłania", changes, [], 0,
                               test_mode=storage.load_write_shape() is None, allow_save_as_drafts=False)
        if dialog.exec() == ChangesDialog.SEND:
            self._start_send(changes)

    def _start_send(self, changes: list[Change]) -> None:
        client = self._create_client()
        if client is None:
            return
        self._send_client = client
        known_shape = storage.load_write_shape()
        job = lambda on_page, should_stop: send_changes(client, changes, known_shape, on_page, should_stop)
        self._start_fetch(job, "zmian do IdoSella", self._on_send_done)

    def _on_send_done(self, report: SendReport) -> None:
        if report.shape and report.count(OK) and storage.load_write_shape() is None:
            storage.save_write_shape(report.shape)       # test udany - zapamiętujemy sposób zapisu
        if report.names_after:
            # Słownik w programie = stan sklepu po zapisie (bez pobierania wszystkiego od nowa)
            storage.update_cached_names(self._raw_elements, report.names_after, LANGUAGES)
            try:
                storage.save_dictionary_cache(self._raw_elements, self._dictionary_downloaded_at)
            except OSError:
                pass   # program i tak pokazuje aktualny stan; najwyżej po restarcie trzeba pobrać słownik
            self._dictionary = ParameterDictionary(self._raw_elements)
            for element_id in report.names_after:          # wysłane = już nie robocze
                self._drafts.pop(element_id, None)
            self._on_draft_changed()
            self._refresh_tabs()
        self._update_status()
        if SendResultDialog(self, report).exec() == SendResultDialog.RESTORE:
            self._restore(report)

    def _restore(self, report: SendReport) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            restored, failed = restore_from_backup(self._send_client, report.outcomes, report.last_shape)
        except Exception as error:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, WINDOW_TITLE, f"Przywracanie nie powiodło się:\n{error}\n\n"
                                                     f"Kopia: {report.backup_path}")
            return
        QApplication.restoreOverrideCursor()
        QMessageBox.information(self, WINDOW_TITLE, f"Przywrócono: {restored}, nie udało się: {failed}.\n"
                                                    f"Kopia: {report.backup_path}\n\nPobierz słownik ponownie, "
                                                    f"żeby zobaczyć aktualny stan.")

    # --- pobieranie w tle ---
    def _create_client(self) -> IdoSellClient | None:
        try:
            return IdoSellClient(os.getenv("IDOSELL_SHOP_URL", ""), os.getenv("IDOSELL_API_KEY", ""))
        except ValueError as error:
            QMessageBox.warning(self, WINDOW_TITLE, str(error))
            return None

    def _start_dictionary_fetch(self) -> None:
        client = self._create_client()
        if client:
            self._start_fetch(client.fetch_whole_dictionary, "słownika", self._on_dictionary_ready)

    def _read_product_limit(self) -> int | None:
        """Tekst z listy „Ile” -> liczba albo None (= wszystkie). Zły tekst -> ValueError."""
        text = self._product_limit_combo.currentText().strip()
        if text.lower() in ("", ALL_PRODUCTS.lower()):
            return None
        if text.isdigit() and int(text) > 0:
            return int(text)
        raise ValueError(f"„{text}” to nie jest liczba. Wpisz np. 200 albo wybierz „{ALL_PRODUCTS}”.")

    def _start_products_fetch(self) -> None:
        try:
            limit = self._read_product_limit()
        except ValueError as error:
            QMessageBox.warning(self, WINDOW_TITLE, str(error))
            return
        client = self._create_client()
        if client:
            self._pending_products_limit = limit
            # Zamiana na odchudzone obiekty Product dzieje się jeszcze w wątku roboczym
            job = lambda on_page, should_stop: [Product.from_api(raw) for raw in
                                                client.fetch_all_products(on_page, should_stop, limit)]
            what = f"produktów (limit {limit})" if limit else "wszystkich produktów"
            self._start_fetch(job, what, self._on_products_ready)

    def _cancel_fetch(self) -> None:
        if self._fetch_worker is not None:
            self._fetch_worker.stop()
            self._cancel_action.setEnabled(False)
            self._status_label.setText("Przerywanie – kończę bieżącą stronę…")

    def _start_fetch(self, job: Job, what: str, on_ready: Callable[[object], None]) -> None:
        """on_ready MUSI być metodą okna (nie lambdą) - wtedy Qt wywoła ją w głównym wątku."""
        self._fetch_what = what
        for action in self._busy_actions():
            action.setEnabled(False)
        self._cancel_action.setEnabled(True)
        self._progress_bar.setRange(0, 0)   # „kręcący się” pasek, dopóki nie znamy liczby stron
        self._progress_bar.show()
        self._status_label.setText(f"Praca w tle: {what}…")

        self._fetch_thread = QThread(self)
        self._fetch_worker = FetchWorker(job)
        self._fetch_worker.moveToThread(self._fetch_thread)   # run() wykona się w nowym wątku
        self._fetch_thread.started.connect(self._fetch_worker.run)
        self._fetch_worker.progress.connect(self._on_fetch_progress)
        self._fetch_worker.result_ready.connect(on_ready)
        self._fetch_worker.failed.connect(self._on_fetch_failed)
        self._fetch_worker.cancelled.connect(self._on_fetch_cancelled)
        for signal in (self._fetch_worker.result_ready, self._fetch_worker.failed, self._fetch_worker.cancelled):
            signal.connect(self._fetch_thread.quit)
        self._fetch_thread.finished.connect(self._on_fetch_thread_finished)
        self._fetch_thread.start()

    def _busy_actions(self) -> list:
        """Przyciski wyłączane na czas pracy w tle (nie da się np. wysyłać w trakcie pobierania)."""
        return [self._fetch_dictionary_action, self._fetch_products_action, self._send_action,
                self._parameters_tab.excel_bar.import_button, self._tree_tab.excel_bar.import_button]

    def _on_fetch_progress(self, done: int, total: int) -> None:
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(done)
        self._status_label.setText(f"Praca w tle ({self._fetch_what}): krok {done} z {total}…")

    def _on_dictionary_ready(self, raw_elements: dict[int, dict]) -> None:
        downloaded_at = datetime.now()
        try:
            storage.save_dictionary_cache(raw_elements, downloaded_at)
        except OSError as error:
            QMessageBox.warning(self, WINDOW_TITLE, f"Słownik pobrany, ale nie zapisał się na dysku:\n{error}")
        self._raw_elements = raw_elements
        self._dictionary = ParameterDictionary(raw_elements)
        self._dictionary_downloaded_at = downloaded_at
        self._refresh_tabs()

    def _on_products_ready(self, products: list[Product]) -> None:
        downloaded_at = datetime.now()
        try:
            storage.save_products_cache(products, downloaded_at, self._pending_products_limit)
        except OSError as error:
            QMessageBox.warning(self, WINDOW_TITLE, f"Produkty pobrane, ale nie zapisały się na dysku:\n{error}")
        self._products = ProductIndex(products)
        self._products_downloaded_at = downloaded_at
        self._products_limit = self._pending_products_limit
        self._refresh_tabs()
        if self._dictionary is None:
            self._status_label.setText(f"Pobrano {len(products)} produktów. Teraz pobierz słownik.")

    def _on_fetch_failed(self, message: str) -> None:
        QMessageBox.critical(self, WINDOW_TITLE, f"Operacja ({self._fetch_what}) nie powiodła się – "
                                                 f"nic się nie zmieniło.\n\n{message}")
        self._update_status()

    def _on_fetch_cancelled(self) -> None:
        self._update_status()
        self.statusBar().showMessage(f"Przerwano ({self._fetch_what}) – poprzednie dane zostały bez zmian.",
                                     8000)

    def _on_fetch_thread_finished(self) -> None:
        self._progress_bar.hide()
        self._cancel_action.setEnabled(False)
        for action in self._busy_actions():
            action.setEnabled(True)
        self._fetch_worker.deleteLater()
        self._fetch_thread.deleteLater()
        self._fetch_worker = self._fetch_thread = None

    def closeEvent(self, event) -> None:
        """Przy zamykaniu w trakcie pobierania: prosimy wątek o zatrzymanie i czekamy, aż skończy bieżącą stronę."""
        if self._fetch_thread is not None and self._fetch_thread.isRunning():
            self._status_label.setText("Przerywanie pobierania…")
            self._fetch_worker.stop()
            self._fetch_thread.quit()
            self._fetch_thread.wait()
        super().closeEvent(event)
