from feaston.shop import Product, build_cart_items, delivery_charge, find_product, load_products, save_products, subtotal_from_cart_items


def test_delivery_is_free_within_5km():
    assert delivery_charge(4.9, is_delivery=True) == 0
    assert delivery_charge(5.0, is_delivery=True) == 0


def test_delivery_charge_applies_beyond_5km():
    assert delivery_charge(5.1, is_delivery=True) == 10
    assert delivery_charge(7.0, is_delivery=True) == 20


def test_pickup_has_no_delivery_charge():
    assert delivery_charge(15.0, is_delivery=False) == 0


def test_cart_subtotal_from_catalog():
    cart_items = build_cart_items({"spice-cardamom": 2, "craft-coir-basket": 1})
    assert subtotal_from_cart_items(cart_items) == (320 * 2) + 480


def test_save_and_load_custom_products(tmp_path):
    store_path = tmp_path / "products.json"
    products = [
        Product(
            id="craft-test",
            name="Test Craft",
            category="Handicrafts",
            unit_price=999,
            unit_label="1 piece",
            description="Sample",
            image_path="uploads/products/test.png",
        )
    ]
    save_products(store_path, products)
    loaded = load_products(store_path)
    assert len(loaded) == 1
    assert loaded[0].image_path == "uploads/products/test.png"
    assert find_product("craft-test", loaded) is not None
