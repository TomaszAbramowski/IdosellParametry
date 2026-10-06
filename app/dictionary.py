"""Słownik parametrów IdoSella: elementy (sekcje, parametry, wartości) i stan ich tłumaczenia PL -> EN.

Ten moduł nie wie nic o API ani o GUI - tylko zamienia surowe dane z IdoSella na wygodne obiekty.
"""
import re
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from functools import cached_property

POLISH = "pol"
ENGLISH = "eng"
LANGUAGES = [POLISH, ENGLISH]   # obecnie pracujemy tylko na parze polski - angielski

TYPE_SECTION = "section"
TYPE_PARAMETER = "parameter"
TYPE_VALUE = "value"
TYPE_LABELS = {TYPE_SECTION: "sekcja", TYPE_PARAMETER: "parametr", TYPE_VALUE: "wartość"}

PLACEHOLDER_PREFIX = "dane do uzupełni"
PLACEHOLDER_NAMES = {"brak danych", "b/d", "b.d.", "bd", "?", "do uzupełnienia"}   # inne „zaślepki”
NONE_NAMES = {"brak"}               # może być poprawną wartością („Dysza: brak”) albo zaślepką - do sprawdzenia
LETTER = re.compile(r"[^\W\d_]")   # dowolna litera (także ą, ę, ż...), bez cyfr i „_”
LETTER_OR_DIGIT = re.compile(r"[^\W_]")
DIGIT = re.compile(r"\d")
WORD = re.compile(r"[^\W\d_]+")            # ciąg liter, np. „ft”, „lbs”, „strzałowy”
# Jednostki i oznaczenia, które są takie same po polsku i po angielsku: „44.2 J / 32.6 ft.lbs”, „5.5 mm (.22)”.
# Celowo BEZ polskich skrótów, które trzeba tłumaczyć (np. „szt” -> „pcs”, „do” w „do 17J”).
UNIT_WORDS = {
    "mm", "cm", "dm", "m", "km", "in", "inch", "ft", "yd",          # długość
    "g", "kg", "mg", "gr", "oz", "lb", "lbs",                       # masa (gr = grain)
    "j", "ft", "lbf", "fpe", "w", "kw", "v", "mah", "ah", "wh",     # energia, moc, prąd
    "bar", "psi", "atm", "kpa", "mpa",                              # ciśnienie
    "ml", "l", "cl", "cc",                                          # objętość
    "s", "ms", "h", "min", "fps", "kmh", "rpm",                     # czas, prędkość
    "cal", "x", "moa", "mrad", "mil", "lm", "lx", "nm", "hz", "db", "shu", "ip", "led", "mp", "mpx",
    "co", "pcp", "hrc", "c", "f", "k",                              # CO2, PCP, twardość HRC, °C
}


def is_number_with_units(text: str) -> bool:
    """„44.2 J / 32.6 ft.lbs”, „5.5 mm (.22)”, „200 bar”, „3-9x40” -> True (nie trzeba tłumaczyć).
    „6-cio strzałowy”, „do 17J”, „10 szt.” -> False."""
    if not DIGIT.search(text):
        return False
    return all(word.lower() in UNIT_WORDS for word in WORD.findall(text))


class PolishState(Enum):
    """Stan nazwy POLSKIEJ - czy w ogóle jest co tłumaczyć."""
    OK = "OK"
    EMPTY = "Pusta"
    PLACEHOLDER = "Dane do uzupełnienia"
    NONE_VALUE = "„brak” – sprawdź"


POLISH_PROBLEMS = frozenset({PolishState.EMPTY, PolishState.PLACEHOLDER})


def evaluate_polish(polish: str) -> PolishState:
    lowered = polish.strip().lower()
    if not LETTER_OR_DIGIT.search(lowered):          # "", "-", "—", "..."
        return PolishState.EMPTY
    if lowered.startswith(PLACEHOLDER_PREFIX) or lowered in PLACEHOLDER_NAMES:
        return PolishState.PLACEHOLDER
    if lowered in NONE_NAMES:
        return PolishState.NONE_VALUE
    return PolishState.OK


class TranslationState(Enum):
    """Stan nazwy angielskiej W SKLEPIE. Tekst (wartość) jest pokazywany w programie."""
    MISSING = "Do tłumaczenia – brak angielskiego"
    SAME_AS_POLISH = "Do tłumaczenia – jest po polsku"
    PLACEHOLDER = "Brak danych („dane do uzupełnienia”)"
    NUMBER_OR_CODE = "Nie trzeba – liczba / jednostka"
    TRANSLATED = "Przetłumaczone"


NEEDS_TRANSLATION = frozenset({TranslationState.MISSING, TranslationState.SAME_AS_POLISH})


def evaluate_translation(polish: str, english: str) -> TranslationState:
    if not english:
        return TranslationState.MISSING
    if polish.lower().startswith(PLACEHOLDER_PREFIX) or english.lower().startswith(PLACEHOLDER_PREFIX):
        return TranslationState.PLACEHOLDER
    if english == polish:
        # „Nie” == „Nie” -> nieprzetłumaczone; „129”, „200 bar”, „44.2 J / 32.6 ft.lbs” -> nie ma czego tłumaczyć
        if not LETTER.search(polish) or is_number_with_units(polish):
            return TranslationState.NUMBER_OR_CODE
        return TranslationState.SAME_AS_POLISH
    return TranslationState.TRANSLATED


@dataclass(frozen=True)
class DictionaryElement:
    """Jeden element słownika. frozen=True -> obiekt jest niezmienny (jak final w Javie)."""
    id: int
    type: str
    parameter_id: int            # przy wartości: ID parametru, do którego należy; przy parametrze/sekcji: 0
    name_pl: str
    name_en: str
    value_ids: tuple[int, ...]

    @classmethod
    def from_api(cls, raw: dict) -> "DictionaryElement":
        names = {name.get("languageId"): (name.get("value") or "").strip() for name in raw.get("names") or []}
        return cls(
            id=int(raw["id"]),
            type=raw.get("type") or "",
            parameter_id=int(raw.get("parameterId") or 0),
            name_pl=names.get(POLISH, ""),
            name_en=names.get(ENGLISH, ""),
            value_ids=tuple(int(value_id) for value_id in raw.get("parameterValueIds") or []),
        )

    @cached_property   # liczone raz, przy pierwszym użyciu - tabela pyta o to tysiące razy
    def state(self) -> TranslationState:
        return evaluate_translation(self.name_pl, self.name_en)

    @cached_property
    def polish_state(self) -> PolishState:
        return evaluate_polish(self.name_pl)

    @property
    def has_polish_problem(self) -> bool:
        return self.polish_state in POLISH_PROBLEMS

    @property
    def needs_translation(self) -> bool:
        """Do tłumaczenia tylko wtedy, gdy po polsku jest coś sensownego - zaślepki nie tłumaczymy."""
        return self.state in NEEDS_TRANSLATION and not self.has_polish_problem


class ParameterDictionary:
    """Cały słownik: osobna lista parametrów (razem z sekcjami) i osobna lista wartości."""

    def __init__(self, raw_elements: dict):
        self.elements = {int(element_id): DictionaryElement.from_api(raw) for element_id, raw in raw_elements.items()}
        self.parameters = sorted((e for e in self.elements.values() if e.type != TYPE_VALUE),
                                 key=lambda element: element.name_pl.lower())
        self.values = sorted((e for e in self.elements.values() if e.type == TYPE_VALUE),
                             key=lambda value: (self.parent_name(value).lower(), value.name_pl.lower()))
        # Counter = słownik „klucz -> ile razy”; brakujący klucz daje 0 zamiast błędu
        self._untranslated_values = Counter(value.parameter_id for value in self.values if value.needs_translation)
        self._all_values = Counter(value.parameter_id for value in self.values)
        self._polish_problem_values = Counter(value.parameter_id for value in self.values if value.has_polish_problem)

    def parent_of(self, value: DictionaryElement) -> DictionaryElement | None:
        return self.elements.get(value.parameter_id)

    def parent_name(self, value: DictionaryElement) -> str:
        parent = self.parent_of(value)
        return parent.name_pl if parent else ""

    def parent_name_en(self, value: DictionaryElement) -> str:
        parent = self.parent_of(value)
        return parent.name_en if parent else ""

    def value_count_of(self, parameter_id: int) -> int:
        return self._all_values[parameter_id]

    def untranslated_values_of(self, parameter_id: int) -> int:
        return self._untranslated_values[parameter_id]

    def polish_problem_values_of(self, parameter_id: int) -> int:
        return self._polish_problem_values[parameter_id]

    def parameters_with_values(self) -> list[DictionaryElement]:
        return [parameter for parameter in self.parameters if self._all_values[parameter.id]]
