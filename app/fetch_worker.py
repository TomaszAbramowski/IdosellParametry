"""Pobieranie w osobnym wątku, żeby okno nie „zamarzało”.

Worker dostaje „zadanie” - funkcję job(on_page, should_stop) - i wykonuje ją w tle.
Wątek roboczy NIE może dotykać widżetów - wysyła tylko sygnały. Qt dostarcza je do okna
w głównym wątku (bezpiecznie), a okno aktualizuje pasek postępu i tabele.
"""
from typing import Callable

from PySide6.QtCore import QObject, Signal, Slot

from app.idosell_api import FetchCancelled, ProgressCallback, StopCheck

Job = Callable[[ProgressCallback, StopCheck], object]


class FetchWorker(QObject):
    progress = Signal(int, int)        # pobrane strony, wszystkie strony
    result_ready = Signal(object)      # „object”, żeby Qt nie przerabiał pythonowych danych
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, job: Job):
        super().__init__()
        self._job = job
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True    # sprawdzane przed każdą stroną

    @Slot()
    def run(self) -> None:
        try:
            result = self._job(self.progress.emit, lambda: self._stop_requested)
        except FetchCancelled:
            self.cancelled.emit()
        except Exception as error:     # każdy błąd pokazujemy w oknie zamiast wywracać program
            self.failed.emit(str(error))
        else:
            self.result_ready.emit(result)
