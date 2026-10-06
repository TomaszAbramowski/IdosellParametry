"""Zmiana = „element X: EN było A, ma być B”. Wspólny format dla Excela i tłumaczeń roboczych."""
from dataclasses import dataclass

from app.dictionary import TYPE_LABELS, TYPE_VALUE, DictionaryElement, ParameterDictionary
from app.natural_sort import natural_sorted


@dataclass(frozen=True)
class Change:
    element_id: int
    kind: str          # „parametr”, „sekcja”, „wartość”
    parameter: str     # przy wartości: nazwa parametru, do którego należy; przy parametrze: puste
    name_pl: str
    old_en: str        # EN w sklepie (według pobranego słownika)
    new_en: str        # EN, które ma być
    note: str = ""     # ostrzeżenie do pokazania w podglądzie


def make_change(element: DictionaryElement, dictionary: ParameterDictionary, new_en: str, note: str = "") -> Change:
    parameter = dictionary.parent_name(element) if element.type == TYPE_VALUE else ""
    return Change(element.id, TYPE_LABELS.get(element.type, element.type), parameter, element.name_pl,
                  element.name_en, new_en.strip(), note)


def changes_from_drafts(drafts: dict[int, str], dictionary: ParameterDictionary) -> list[Change]:
    """Tłumaczenia robocze, które RÓŻNIĄ SIĘ od tego, co jest w sklepie."""
    changes = []
    for element_id, text in drafts.items():
        element = dictionary.elements.get(element_id)
        if element is not None and text.strip() and text.strip() != element.name_en:
            changes.append(make_change(element, dictionary, text))
    return sort_changes(changes)


def sort_changes(changes: list[Change]) -> list[Change]:
    """Najpierw parametry i sekcje, potem wartości - każde alfabetycznie."""
    ordered = natural_sorted(natural_sorted(changes, lambda c: c.name_pl), lambda c: c.parameter)
    return [c for c in ordered if c.kind != "wartość"] + [c for c in ordered if c.kind == "wartość"]
