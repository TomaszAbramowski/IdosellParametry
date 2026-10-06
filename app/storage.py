"""Pliki lokalne: kopia pobranego słownika (żeby nie pobierać przy każdym starcie) i robocze tłumaczenia.

Robocze tłumaczenia = to, co wpiszesz w kolumnie „Po angielsku – NOWE”. Na razie NIE trafiają do IdoSella,
są tylko zapisywane w pliku (po każdej zmianie, więc nic nie zginie przy zamknięciu programu).
"""
import json
from datetime import datetime
from pathlib import Path

from app.products import Product

PROJECT_DIRECTORY = Path(__file__).resolve().parent.parent
DATA_DIRECTORY = PROJECT_DIRECTORY / "exports"
CACHE_FILE = DATA_DIRECTORY / "slownik_cache.json"
DRAFTS_FILE = DATA_DIRECTORY / "tlumaczenia_robocze.json"
PRODUCTS_FILE = DATA_DIRECTORY / "produkty_cache.json"
BACKUP_DIRECTORY = DATA_DIRECTORY / "backups"                  # kopie elementów sprzed każdego zapisu
WRITE_SETTINGS_FILE = DATA_DIRECTORY / "zapis_ustawienia.json"  # zapamiętany, sprawdzony sposób zapisu
BACKUP_DIRECTORY = DATA_DIRECTORY / "backups"


def _write_json_safely(path: Path, data) -> None:
    """Najpierw zapis do pliku tymczasowego, potem podmiana - przy awarii nie zostanie pół pliku."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def save_dictionary_cache(raw_elements: dict[int, dict], downloaded_at: datetime) -> None:
    _write_json_safely(CACHE_FILE, {"downloadedAt": downloaded_at.isoformat(timespec="seconds"),
                                    "elements": raw_elements})


def load_dictionary_cache() -> tuple[dict[int, dict], datetime] | None:
    if not CACHE_FILE.exists():
        return None
    data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    # JSON ma klucze tylko tekstowe - zamieniamy z powrotem na liczby
    elements = {int(element_id): element for element_id, element in data["elements"].items()}
    return elements, datetime.fromisoformat(data["downloadedAt"])


def load_drafts() -> dict[int, str]:
    if not DRAFTS_FILE.exists():
        return {}
    data = json.loads(DRAFTS_FILE.read_text(encoding="utf-8"))
    return {int(element_id): text for element_id, text in data.items()}


def backup_drafts() -> Path | None:
    """Kopia pliku z tłumaczeniami (np. przed importem z Excela). None, gdy nie ma czego kopiować."""
    if not DRAFTS_FILE.exists():
        return None
    BACKUP_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIRECTORY / f"tlumaczenia_robocze_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_bytes(DRAFTS_FILE.read_bytes())
    return path


def save_drafts(drafts: dict[int, str]) -> None:
    _write_json_safely(DRAFTS_FILE, {str(element_id): text for element_id, text in sorted(drafts.items())})


def save_products_cache(products: list[Product], downloaded_at: datetime, limit: int | None) -> None:
    """Zapisujemy tylko „odchudzone” produkty (nazwa, kategoria, ID parametrów/wartości), bez opisów.
    limit - z jakim limitem pobrano (None = wszystkie), żeby było wiadomo, że to tylko próbka."""
    _write_json_safely(PRODUCTS_FILE, {"downloadedAt": downloaded_at.isoformat(timespec="seconds"), "limit": limit,
                                       "products": [product.to_cache() for product in products]})


def load_products_cache() -> tuple[list[Product], datetime, int | None] | None:
    if not PRODUCTS_FILE.exists():
        return None
    data = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))
    return ([Product.from_cache(item) for item in data["products"]], datetime.fromisoformat(data["downloadedAt"]),
            data.get("limit"))


def load_write_shape() -> str | None:
    """Sposób zapisu potwierdzony udanym testem; None = jeszcze nie testowano (pierwszy zapis będzie testem)."""
    if not WRITE_SETTINGS_FILE.exists():
        return None
    return json.loads(WRITE_SETTINGS_FILE.read_text(encoding="utf-8")).get("shape")


def save_write_shape(shape: str) -> None:
    _write_json_safely(WRITE_SETTINGS_FILE, {"shape": shape, "verifiedAt": datetime.now().isoformat(timespec="seconds")})


def update_cached_names(raw_elements: dict[int, dict], names_after: dict[int, dict[str, str]],
                        languages: list[str]) -> None:
    """Po udanym zapisie: podmienia nazwy w pobranym słowniku (w pamięci), żeby nie pobierać go od nowa."""
    for element_id, names in names_after.items():
        if element_id in raw_elements:
            raw_elements[element_id]["names"] = [{"languageId": language, "value": names.get(language, "")}
                                                 for language in languages]
