"""Produkty IdoSella - tylko to, czego potrzebujemy: nazwa, kategoria i przypięte parametry / wartości.

ProductIndex odpowiada na pytania „które produkty używają tej wartości?” i „w jakich kategoriach?”.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass

from app.dictionary import POLISH


@dataclass(frozen=True)
class Product:
    id: int
    code: str
    name: str
    category: str
    parameter_ids: tuple[int, ...]
    value_ids: tuple[int, ...]

    @classmethod
    def from_api(cls, raw: dict) -> "Product":
        names = {data.get("langId"): (data.get("productName") or "").strip()
                 for data in raw.get("productDescriptionsLangData") or []}
        name = names.get(POLISH) or next((text for text in names.values() if text), "")
        parameter_ids, value_ids = [], []
        for parameter in raw.get("productParameters") or []:
            if parameter.get("parameterId"):
                parameter_ids.append(int(parameter["parameterId"]))
            for value in parameter.get("parameterValues") or []:
                if value.get("parameterValueId"):
                    value_ids.append(int(value["parameterValueId"]))
        return cls(
            id=int(raw["productId"]),
            code=str(raw.get("productDisplayedCode") or ""),
            name=name,
            category=(raw.get("categoryName") or "").strip(),
            parameter_ids=tuple(dict.fromkeys(parameter_ids)),   # dict.fromkeys = usuwa powtórzenia, zachowuje kolejność
            value_ids=tuple(dict.fromkeys(value_ids)),
        )

    def to_cache(self) -> dict:
        return {"id": self.id, "code": self.code, "name": self.name, "category": self.category,
                "parameterIds": list(self.parameter_ids), "valueIds": list(self.value_ids)}

    @classmethod
    def from_cache(cls, data: dict) -> "Product":
        return cls(id=int(data["id"]), code=data.get("code", ""), name=data.get("name", ""),
                   category=data.get("category", ""), parameter_ids=tuple(data.get("parameterIds", [])),
                   value_ids=tuple(data.get("valueIds", [])))


class ProductIndex:
    """Odwrotny spis: ID wartości -> produkty, które ją mają (raz liczone, potem szybkie odpowiedzi)."""

    def __init__(self, products: list[Product]):
        self.products = products
        by_value: dict[int, list[Product]] = defaultdict(list)
        for product in products:
            for value_id in product.value_ids:
                by_value[value_id].append(product)
        self._by_value = {value_id: sorted(items, key=lambda p: p.name.lower()) for value_id, items in by_value.items()}
        self._categories_by_value = {value_id: tuple(sorted({p.category for p in items if p.category}, key=str.lower))
                                     for value_id, items in self._by_value.items()}
        self._products_by_parameter = Counter(parameter_id for p in products for parameter_id in p.parameter_ids)
        self.categories = sorted({p.category for p in products if p.category}, key=str.lower)

    def products_with(self, value_id: int) -> list[Product]:
        return self._by_value.get(value_id, [])

    @property
    def used_value_ids(self) -> set[int]:
        """ID wartości, które ma przynajmniej jeden z pobranych produktów."""
        return set(self._by_value)

    def products_with_all(self, value_ids: list[int]) -> list[Product]:
        """Produkty, które mają WSZYSTKIE podane wartości naraz (część wspólna zbiorów)."""
        if not value_ids:
            return []
        common = {p.id: p for p in self.products_with(value_ids[0])}
        for value_id in value_ids[1:]:
            ids = {p.id for p in self.products_with(value_id)}
            common = {product_id: p for product_id, p in common.items() if product_id in ids}
        return sorted(common.values(), key=lambda p: p.name.lower())

    def categories_of(self, value_id: int) -> tuple[str, ...]:
        return self._categories_by_value.get(value_id, ())

    def product_count_with_parameter(self, parameter_id: int) -> int:
        return self._products_by_parameter[parameter_id]
