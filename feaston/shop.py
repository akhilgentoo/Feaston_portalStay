"""Product catalog and cart calculations for the FeastOn shop page."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from math import ceil
from pathlib import Path


@dataclass(slots=True)
class Product:
    id: str
    name: str
    category: str
    unit_price: int
    unit_label: str
    description: str
    image_path: str = ""


DEFAULT_CATALOG: tuple[Product, ...] = (
    Product(
        id="spice-cardamom",
        name="Green Cardamom",
        category="Spices",
        unit_price=320,
        unit_label="100 g pack",
        description="Aromatic cardamom pods from Kerala highland farms.",
    ),
    Product(
        id="spice-pepper",
        name="Malabar Black Pepper",
        category="Spices",
        unit_price=240,
        unit_label="100 g pack",
        description="Bold whole peppercorns with signature Malabar heat.",
    ),
    Product(
        id="spice-clove",
        name="Premium Clove",
        category="Spices",
        unit_price=210,
        unit_label="75 g pack",
        description="Hand-selected sun-dried cloves for deep flavor.",
    ),
    Product(
        id="craft-coir-basket",
        name="Handwoven Coir Basket",
        category="Handicrafts",
        unit_price=480,
        unit_label="1 piece",
        description="Traditional palm and coir weave by local artisans.",
    ),
    Product(
        id="craft-brass-lamp",
        name="Mini Brass Nilavilakku",
        category="Handicrafts",
        unit_price=920,
        unit_label="1 piece",
        description="Compact Kerala-style brass lamp for prayer corners.",
    ),
    Product(
        id="craft-kathakali-mask",
        name="Kathakali Wall Mask",
        category="Handicrafts",
        unit_price=760,
        unit_label="1 piece",
        description="Colorful handcrafted wall art inspired by Kathakali.",
    ),
)


def product_to_dict(product: Product) -> dict:
    payload = asdict(product)
    payload["unit_price"] = int(product.unit_price)
    return payload


def product_from_dict(payload: dict) -> Product:
    return Product(
        id=str(payload.get("id", "")).strip(),
        name=str(payload.get("name", "")).strip(),
        category=str(payload.get("category", "Spices")).strip() or "Spices",
        unit_price=int(payload.get("unit_price", 0)),
        unit_label=str(payload.get("unit_label", "1 unit")).strip() or "1 unit",
        description=str(payload.get("description", "")).strip(),
        image_path=str(payload.get("image_path", "")).strip(),
    )


def save_products(store_path: Path, products: list[Product]) -> None:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = [product_to_dict(product) for product in products]
    store_path.write_text(json.dumps(serialized, ensure_ascii=True, indent=2), encoding="utf-8")


def load_products(store_path: Path) -> list[Product]:
    if not store_path.exists():
        defaults = list(DEFAULT_CATALOG)
        save_products(store_path, defaults)
        return defaults

    try:
        payload = json.loads(store_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = []

    products: list[Product] = []
    if isinstance(payload, list):
        for row in payload:
            if not isinstance(row, dict):
                continue
            product = product_from_dict(row)
            if product.id and product.name and product.unit_price > 0:
                products.append(product)

    if products:
        return products

    defaults = list(DEFAULT_CATALOG)
    save_products(store_path, defaults)
    return defaults


def product_index(products: list[Product] | None = None) -> dict[str, Product]:
    chosen = products if products is not None else list(DEFAULT_CATALOG)
    return {product.id: product for product in chosen}


def find_product(product_id: str, products: list[Product] | None = None) -> Product | None:
    return product_index(products).get(product_id)


def normalize_cart(cart_payload: dict[str, int] | None, products: list[Product] | None = None) -> dict[str, int]:
    if not cart_payload:
        return {}
    catalog_by_id = product_index(products)
    normalized: dict[str, int] = {}
    for product_id, qty in cart_payload.items():
        if product_id not in catalog_by_id:
            continue
        if qty <= 0:
            continue
        normalized[product_id] = int(qty)
    return normalized


def build_cart_items(cart_payload: dict[str, int] | None, products: list[Product] | None = None) -> list[dict]:
    catalog = products if products is not None else list(DEFAULT_CATALOG)
    cart = normalize_cart(cart_payload, catalog)
    rows: list[dict] = []
    for product in catalog:
        quantity = cart.get(product.id, 0)
        if quantity <= 0:
            continue
        line_total = product.unit_price * quantity
        rows.append(
            {
                "id": product.id,
                "name": product.name,
                "category": product.category,
                "unit_price": product.unit_price,
                "unit_label": product.unit_label,
                "image_path": product.image_path,
                "quantity": quantity,
                "line_total": line_total,
            }
        )
    return rows


def subtotal_from_cart_items(cart_items: list[dict]) -> int:
    return sum(item["line_total"] for item in cart_items)


def delivery_charge(distance_km: float, is_delivery: bool) -> int:
    if not is_delivery:
        return 0
    if distance_km <= 5:
        return 0
    return ceil(distance_km - 5) * 10
