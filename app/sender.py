"""Wysyłanie zmian EN do IdoSella. Dla każdej paczki (do 20 elementów):

1. ODCZYT elementów ze WSZYSTKIMI językami i zapis KOPII do pliku (zanim cokolwiek wyślemy),
2. KONTROLA: jeśli w sklepie EN jest już inne niż w podglądzie (ktoś zmienił w panelu) - pomijamy,
3. ZAPIS: wysyłamy wszystkie nazwy elementu, zmienione jest tylko „eng”,
4. WERYFIKACJA: ponowny odczyt - EN musi być nowe, a pozostałe języki dokładnie takie jak przed zapisem.

Pierwszy zapis w historii programu (brak zapamiętanego „kształtu” zapisu) to TEST: wysyłany jest
tylko JEDEN element. Po udanej weryfikacji kolejne wysyłki idą już normalnie, paczkami.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.changes import Change
from app.idosell_api import (MAX_ITEMS_PER_WRITE, WRITE_LANGUAGE_KEY, WRITE_SHAPES, IdoSellClient,
                             ProgressCallback, StopCheck)
from app.storage import BACKUP_DIRECTORY

ENGLISH = "eng"
OK, SKIPPED, FAILED = "Zapisano", "Pominięto", "Błąd"
REJECTED_CODES = {400, 422}     # zapis odrzucony w całości -> w sklepie nic się nie zmieniło


@dataclass
class Outcome:
    change: Change
    status: str
    message: str = ""
    names_before: dict[str, str] | None = None     # do ewentualnego przywrócenia


@dataclass
class SendReport:
    outcomes: list[Outcome] = field(default_factory=list)
    backup_path: Path | None = None
    shape: str | None = None              # kształt zapisu, który zadziałał
    last_shape: str | None = None         # kształt ostatniego zapisu, który doszedł (do przywracania)
    test_mode: bool = False
    names_after: dict[int, dict[str, str]] = field(default_factory=dict)   # do odświeżenia słownika

    def count(self, status: str) -> int:
        return sum(1 for o in self.outcomes if o.status == status)


def names_by_language(raw: dict) -> dict[str, str]:
    return {name.get("languageId"): name.get("value") or "" for name in raw.get("names") or []}


def build_item(element_id: int, names_before: dict[str, str], new_en: str) -> dict:
    """WSZYSTKIE języki z odczytu, podmienione tylko „eng” - bezpieczne niezależnie od tego,
    czy IdoSell przy zapisie zastępuje całą listę nazw, czy tylko podane języki."""
    names = [{WRITE_LANGUAGE_KEY: language, "value": new_en if language == ENGLISH else value}
             for language, value in names_before.items()]
    if ENGLISH not in names_before:
        names.append({WRITE_LANGUAGE_KEY: ENGLISH, "value": new_en})
    return {"id": element_id, "names": names}


def response_faults(text: str) -> dict[int, str]:
    """Błędy z odpowiedzi IdoSella: {"results": [{"item_id": 1, "faultCode": 5, "faultString": "..."}]}.
    Klucz 0 = błąd całej operacji („errors”)."""
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    faults = {}
    for result in data.get("results") or []:
        if isinstance(result, dict) and result.get("faultCode"):
            faults[int(result.get("item_id") or 0)] = f"{result.get('faultCode')}: {result.get('faultString', '')}"
    errors = data.get("errors")
    if isinstance(errors, dict) and errors.get("faultCode"):
        faults[0] = f"{errors.get('faultCode')}: {errors.get('faultString', '')}"
    return faults


def send_changes(client: IdoSellClient, changes: list[Change], known_shape: str | None,
                 on_page: ProgressCallback | None = None, should_stop: StopCheck = lambda: False) -> SendReport:
    report = SendReport(test_mode=known_shape is None, shape=known_shape)
    if report.test_mode:
        changes = changes[:1]
    shapes_to_try = [known_shape] if known_shape else list(WRITE_SHAPES)
    report.backup_path = BACKUP_DIRECTORY / f"{datetime.now():%Y%m%d_%H%M%S}_kopia_przed_zapisem.json"
    backup: dict[int, dict] = {}
    batches = [changes[i:i + MAX_ITEMS_PER_WRITE] for i in range(0, len(changes), MAX_ITEMS_PER_WRITE)]

    for number, batch in enumerate(batches, start=1):
        if should_stop():
            for change in [c for b in batches[number - 1:] for c in b]:
                report.outcomes.append(Outcome(change, SKIPPED, "Przerwano przed wysłaniem"))
            break
        before_raw = client.read_elements([c.element_id for c in batch])
        backup.update(before_raw)
        _save_backup(report.backup_path, backup)          # kopia ZANIM cokolwiek wyślemy

        to_write: list[tuple[Change, dict[str, str]]] = []
        for change in batch:
            raw = before_raw.get(change.element_id)
            if raw is None:
                report.outcomes.append(Outcome(change, FAILED, "Nie ma takiego elementu w sklepie"))
                continue
            names = names_by_language(raw)
            current_en = names.get(ENGLISH, "")
            if current_en == change.new_en:
                report.outcomes.append(Outcome(change, SKIPPED, "W sklepie już jest taka nazwa"))
            elif current_en != change.old_en:
                report.outcomes.append(Outcome(change, SKIPPED, f"W sklepie zmieniło się od pobrania słownika "
                                                                f"(teraz „{current_en}”) – pobierz słownik ponownie"))
            else:
                to_write.append((change, names))

        if to_write:
            working_shape = _write_and_verify(client, to_write, shapes_to_try, report)
            if working_shape:
                shapes_to_try = [working_shape]
                report.shape = working_shape
        if on_page:
            on_page(number, len(batches))
    return report


def _write_and_verify(client: IdoSellClient, to_write: list[tuple[Change, dict[str, str]]], shapes: list[str],
                      report: SendReport) -> str | None:
    items = [build_item(change.element_id, names, change.new_en) for change, names in to_write]
    ids = [change.element_id for change, _ in to_write]
    problem = ""
    for shape in shapes:
        status, text = client.write_names(items, shape)
        if status in REJECTED_CODES:
            problem = f"IdoSell odrzucił zapis (kod {status}) – nic się nie zmieniło. {text[:300]}"
            continue                                   # spróbuj drugiego kształtu (tylko przy teście)
        if status >= 400:
            problem = f"IdoSell odrzucił zapis (kod {status}) – nic się nie zmieniło. {text[:300]}"
            break                                      # np. 401/403 - brak uprawnień, drugi kształt nic nie da
        report.last_shape = shape
        faults = response_faults(text)
        after_raw = client.read_elements(ids)
        after = {element_id: names_by_language(after_raw.get(element_id) or {}) for element_id in ids}
        nothing_changed = all(after[change.element_id] == names for change, names in to_write)
        if nothing_changed and shape != shapes[-1]:
            problem = "IdoSell przyjął zapis, ale nic się nie zmieniło"
            continue
        any_ok = False
        for change, names in to_write:
            expected = {**names, ENGLISH: change.new_en}
            actual = after[change.element_id]
            wrong = sorted(lang for lang in set(expected) | set(actual) if actual.get(lang, "") != expected.get(lang, ""))
            if not wrong:
                report.outcomes.append(Outcome(change, OK))
                report.names_after[change.element_id] = actual
                any_ok = True
            else:
                details = ", ".join(f"{lang}: „{actual.get(lang, '')}”" for lang in wrong)
                fault = faults.get(change.element_id) or faults.get(0)
                message = f"Po zapisie niezgodne: {details} (kod {status})"
                if fault:
                    message += f". IdoSell: {fault}"
                elif actual == names:
                    message += f". Nic się nie zmieniło. Odpowiedź IdoSella: {text[:300]}"
                report.outcomes.append(Outcome(change, FAILED, message, names_before=names))
        return shape if any_ok else None
    for change, _ in to_write:
        report.outcomes.append(Outcome(change, FAILED, problem))
    return None


def restore_from_backup(client: IdoSellClient, outcomes: list[Outcome], shape: str) -> tuple[int, int]:
    """Przywraca nazwy sprzed zapisu dla elementów z błędem weryfikacji. Zwraca (przywrócone, nieudane)."""
    to_restore = [o for o in outcomes if o.status == FAILED and o.names_before]
    restored = failed = 0
    for i in range(0, len(to_restore), MAX_ITEMS_PER_WRITE):
        batch = to_restore[i:i + MAX_ITEMS_PER_WRITE]
        items = [{"id": o.change.element_id,
                  "names": [{WRITE_LANGUAGE_KEY: lang, "value": value} for lang, value in o.names_before.items()]}
                 for o in batch]
        client.write_names(items, shape)
        after = client.read_elements([o.change.element_id for o in batch])
        for outcome in batch:
            if names_by_language(after.get(outcome.change.element_id) or {}) == outcome.names_before:
                restored += 1
            else:
                failed += 1
    return restored, failed


def _save_backup(path: Path, backup: dict[int, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
