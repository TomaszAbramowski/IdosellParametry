"""Klient API IdoSella: odczyt słownika i produktów + JEDEN rodzaj zapisu - nazwy elementów słownika.

Strażnik (dwa poziomy):
1. _request() przepuszcza tylko pary (metoda, bramka) z ALLOWED_REQUESTS - usuwanie i inne bramki są zablokowane.
2. write_names() sprawdza treść zapisu: wolno wysłać tylko „id” + „names” (bez opisów, ikon, ustawień),
   najwyżej MAX_ITEMS_PER_WRITE elementów naraz.
"""
import math
import time
from typing import Callable, Iterator

import requests

from app.dictionary import LANGUAGES

API_VERSION = "v8"
PARAMETERS_SEARCH_ENDPOINT = "products/parameters/search"
PRODUCTS_SEARCH_ENDPOINT = "products/products/search"
PARAMETERS_WRITE_ENDPOINT = "products/parameters"           # PUT - edycja elementów słownika
ALLOWED_REQUESTS = {("POST", PARAMETERS_SEARCH_ENDPOINT), ("POST", PRODUCTS_SEARCH_ENDPOINT),
                    ("PUT", PARAMETERS_WRITE_ENDPOINT)}
MAX_ITEMS_PER_WRITE = 20
# Format zapisu według dokumentacji v8 (idosell.readme.io/reference/productsparametersput):
#   {"items": [{"id": 123, "names": [{"lang_id": "eng", "value": "..."}]}]}
# UWAGA: przy ZAPISIE język to „lang_id” (przy odczycie „languageId”) - nieznane pola IdoSell po cichu pomija.
SHAPE_PARAMS = "params"     # {"params": {"items": [...]}}
SHAPE_PLAIN = "plain"       # {"items": [...]}  - zgodny z dokumentacją
WRITE_SHAPES = [SHAPE_PLAIN]
WRITE_LANGUAGE_KEY = "lang_id"
# Tylko potrzebne pola produktu - mniej danych do przesłania
PRODUCT_ELEMENTS = ["lang_data", "code", "category_id", "category_name", "parameters"]
PAGE_LIMIT = 100                                    # maksimum według dokumentacji
PAUSE_BETWEEN_PAGES_SECONDS = 0.5                   # nie obciążamy API
TIMEOUT_SECONDS = 90
MAX_ATTEMPTS = 3                                    # ponowienia przy chwilowych błędach
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}      # 429 = za dużo zapytań, 5xx = błąd po stronie serwera

ProgressCallback = Callable[[int, int], None]       # (pobrane strony, wszystkie strony)
StopCheck = Callable[[], bool]


def _never() -> bool:
    return False


class FetchCancelled(Exception):
    """Pobieranie przerwane przez użytkownika (np. zamknięcie okna)."""


class IdoSellClient:
    def __init__(self, shop_url: str, api_key: str):
        if not shop_url or not api_key:
            raise ValueError("Brak IDOSELL_SHOP_URL albo IDOSELL_API_KEY w pliku .env")
        self._base_url = f"{shop_url.rstrip('/')}/api/admin/{API_VERSION}"
        # Session = jedno połączenie używane wielokrotnie (szybciej niż osobne requests.post)
        self._session = requests.Session()
        self._session.headers.update({"X-API-KEY": api_key, "Accept": "application/json"})

    def fetch_whole_dictionary(self, on_page: ProgressCallback | None = None,
                               should_stop: StopCheck = _never) -> dict[int, dict]:
        params = {"languagesIds": LANGUAGES, "parameterValueIds": True}
        elements = {}
        for data in self._pages(PARAMETERS_SEARCH_ENDPOINT, params, on_page, should_stop):
            for element_id, element in (data.get("parametersResult") or {}).items():
                elements[int(element_id)] = element
        return elements

    def fetch_all_products(self, on_page: ProgressCallback | None = None, should_stop: StopCheck = _never,
                           limit: int | None = None) -> list[dict]:
        """limit=None - wszystkie produkty; limit=50 - tylko pierwsze 50 (szybki podgląd)."""
        page_size = min(PAGE_LIMIT, limit) if limit else PAGE_LIMIT
        max_pages = math.ceil(limit / page_size) if limit else None   # np. 150 -> 2 strony po 100
        params = {"returnElements": PRODUCT_ELEMENTS, "skipDefaultProduct": "y"}
        products = []
        for data in self._pages(PRODUCTS_SEARCH_ENDPOINT, params, on_page, should_stop, page_size, max_pages):
            products.extend(data.get("results") or [])
        return products[:limit] if limit else products

    def read_elements(self, element_ids: list[int]) -> dict[int, dict]:
        """Elementy słownika ze WSZYSTKIMI językami (bez languagesIds) - przed zapisem i do weryfikacji."""
        if not element_ids:
            return {}
        data = self._request("POST", PARAMETERS_SEARCH_ENDPOINT,
                             {"params": {"ids": element_ids, "resultsPage": 0, "resultsLimit": PAGE_LIMIT}})
        return {int(i): element for i, element in (data.get("parametersResult") or {}).items()}

    def write_names(self, items: list[dict], shape: str) -> tuple[int, str]:
        """PUT nazw elementów. items = [{"id": 123, "names": [{"lang_id": "eng", "value": "..."}, ...]}]"""
        ensure_only_names(items)
        body = {"params": {"items": items}} if shape == SHAPE_PARAMS else {"items": items}
        url = f"{self._base_url}/{PARAMETERS_WRITE_ENDPOINT}"
        self._check_allowed("PUT", PARAMETERS_WRITE_ENDPOINT)
        # Zapisu NIE ponawiamy automatycznie - przy błędzie sieci nie wiemy, czy doszedł; sprawdza to weryfikacja
        try:
            response = self._session.put(url, json=body, timeout=TIMEOUT_SECONDS)
        except (requests.ConnectionError, requests.Timeout) as error:
            return 0, f"Błąd połączenia: {error}"
        return response.status_code, response.text

    def _pages(self, endpoint: str, params: dict, on_page: ProgressCallback | None, should_stop: StopCheck,
               page_size: int = PAGE_LIMIT, max_pages: int | None = None) -> Iterator[dict]:
        """Generator: oddaje (yield) kolejne strony wyników, aż do ostatniej (albo do max_pages)."""
        page = 0
        while True:
            if should_stop():
                raise FetchCancelled()
            data = self._request("POST", endpoint, {"params": {**params, "resultsPage": page,
                                                               "resultsLimit": page_size}})
            yield data
            page_count = int(data.get("resultsNumberPage") or 0)
            if max_pages is not None:
                page_count = min(page_count, max_pages)
            page += 1
            if on_page:
                on_page(page, page_count)
            if page >= page_count:
                return
            time.sleep(PAUSE_BETWEEN_PAGES_SECONDS)

    @staticmethod
    def _check_allowed(method: str, endpoint: str) -> None:
        if (method, endpoint) not in ALLOWED_REQUESTS:
            raise PermissionError(f"Strażnik: zapytanie {method} {endpoint} jest zablokowane")

    def _request(self, method: str, endpoint: str, body: dict) -> dict:
        self._check_allowed(method, endpoint)
        if method != "POST":
            raise PermissionError("Strażnik: _request służy tylko do wyszukiwania (POST)")
        url = f"{self._base_url}/{endpoint}"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            last_attempt = attempt == MAX_ATTEMPTS
            try:
                response = self._session.request(method, url, json=body, timeout=TIMEOUT_SECONDS)
            except (requests.ConnectionError, requests.Timeout) as error:
                if last_attempt:
                    raise RuntimeError(f"Brak połączenia z IdoSellem: {error}") from error
            else:
                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError as error:
                        raise RuntimeError(f"IdoSell zwrócił odpowiedź, która nie jest JSON-em: "
                                           f"{response.text[:300]}") from error
                if response.status_code not in RETRY_STATUS_CODES or last_attempt:
                    raise RuntimeError(f"{method} {endpoint}: kod HTTP {response.status_code}: {response.text[:500]}")
            time.sleep(2 ** attempt)   # 2 s, potem 4 s - dajemy serwerowi odetchnąć
        raise AssertionError("nieosiągalne")


def ensure_only_names(items: list[dict]) -> None:
    """Strażnik treści: tylko id + names, names = lista {lang_id, value}, nie więcej niż limit."""
    if not items or len(items) > MAX_ITEMS_PER_WRITE:
        raise PermissionError(f"Strażnik: zapis musi mieć od 1 do {MAX_ITEMS_PER_WRITE} elementów")
    for item in items:
        if set(item) != {"id", "names"} or not isinstance(item["id"], int) or not item["names"]:
            raise PermissionError(f"Strażnik: niedozwolona treść zapisu: {item}")
        for name in item["names"]:
            if set(name) != {WRITE_LANGUAGE_KEY, "value"} or not isinstance(name["value"], str):
                raise PermissionError(f"Strażnik: niedozwolona nazwa w zapisie: {name}")
