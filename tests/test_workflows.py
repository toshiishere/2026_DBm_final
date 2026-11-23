from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import app


def post(client, path, body=None, expected=201):
    result = client.post(path, json=body)
    assert result.status_code == expected, result.text
    return result.json()


def new_order(client, *, items=None, member_id=1, address_id=1):
    return post(
        client,
        "/orders",
        {
            "member_id": member_id,
            "address_id": address_id,
            "shipping_fee": "60.00",
            "items": items or [{"variant_id": 1, "quantity": 2}],
        },
    )


def deliver(client, order):
    post(client, f"/orders/{order['order_id']}/mock-pay", {"success": True})
    shipment = post(client, f"/orders/{order['order_id']}/shipment", {"carrier": "Test Carrier"})
    sid = shipment["shipment_id"]
    for state in ("SHIPPED", "DELIVERED"):
        response = client.patch(
            f"/shipments/{sid}", json={"shipment_status": state, "tracking_number": "TEST-123"}
        )
        assert response.status_code == 200, response.text
    return shipment


def counts(db):
    return {
        table: db.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
        for table in ("orders", "order_item", "inventory_log", "payment", "shipment", "review")
    }


def test_seed_and_read_endpoints(client, db):
    assert (
        db.execute(
            "SELECT count(*) AS n FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE'"
        ).fetchone()["n"]
        == 11
    )
    for table, count in [
        ("member", 12),
        ("member_address", 24),
        ("product", 20),
        ("product_variant", 60),
        ("orders", 40),
    ]:
        assert db.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == count
    for path in (
        "/",
        "/health",
        "/api-info",
        "/docs",
        "/openapi.json",
        "/members",
        "/categories",
        "/members/1/addresses",
        "/members/1/orders",
        "/orders/1",
        "/products",
        "/products/1",
        "/products/1/reviews",
        "/reports/sales",
        "/reports/inventory",
    ):
        assert client.get(path).status_code == 200, path
    assert len(client.get("/products?category_id=1").json()) == 5
    assert len(client.get("/products?q=Canvas").json()) == 1
    assert len(client.get("/products?limit=2&offset=1").json()) == 2


def test_member_address_and_default_switch(client, db):
    member = post(
        client,
        "/members",
        {"member_name": "New Member", "email": "new@example.test", "phone": "0900123456"},
    )
    path = f"/members/{member['member_id']}/addresses"
    data = {
        "recipient_name": "New Member",
        "phone": "0900123456",
        "postal_code": "100",
        "city": "Taipei",
        "district": "Demo",
        "street_address": "1 Demo Road",
        "is_default": True,
    }
    first, second = post(client, path, data), post(client, path, data)
    rows = client.get(path).json()
    assert rows[0]["address_id"] == second["address_id"]
    assert sum(row["is_default"] for row in rows) == 1
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute(
            "UPDATE member_address SET is_default=true WHERE address_id=%s", (first["address_id"],)
        )
    post(
        client,
        "/members",
        {"member_name": "Duplicate", "email": "new@example.test", "phone": "1"},
        expected=409,
    )


def test_catalog_maintenance(client):
    category = post(client, "/categories", {"category_name": "Accessories"})
    product = post(
        client,
        "/products",
        {
            "category_id": category["category_id"],
            "product_name": "Cap",
            "price": "120.25",
            "variants": [{"sku": "CAP-RED", "color": "Red", "size": "M", "stock_qty": 3}],
        },
    )
    pid = product["product_id"]
    assert (
        client.patch(
            f"/products/{pid}", json={"price": "200.00", "is_active": False, "image_url": None}
        ).status_code
        == 200
    )
    assert all(p["product_id"] != pid for p in client.get("/products").json())
    assert any(p["product_id"] == pid for p in client.get("/products?active_only=false").json())
    post(client, f"/products/{pid}/variants", {"sku": "CAP-BLUE", "color": "Blue", "size": "M"})
    assert len(client.get(f"/products/{pid}").json()["variants"]) == 2
    assert client.patch(f"/products/{pid}", json={"price": None}).status_code == 422
    assert client.patch(f"/products/{pid}", json={}).status_code == 422


def test_order_reserves_and_price_snapshot(client, db):
    stock = db.execute("SELECT stock_qty FROM product_variant WHERE variant_id=1").fetchone()[
        "stock_qty"
    ]
    price = db.execute("SELECT price FROM product WHERE product_id=1").fetchone()["price"]
    order = new_order(client)
    assert Decimal(str(order["total_amount"])) == 2 * price + 60
    assert (
        db.execute("SELECT stock_qty FROM product_variant WHERE variant_id=1").fetchone()[
            "stock_qty"
        ]
        == stock - 2
    )
    assert client.patch("/products/1", json={"price": "9999.00"}).status_code == 200
    detail = client.get(f"/orders/{order['order_id']}").json()
    assert Decimal(str(detail["items"][0]["unit_price"])) == price
    log = db.execute(
        "SELECT * FROM inventory_log WHERE order_item_id=%s", (detail["items"][0]["order_item_id"],)
    ).fetchone()
    assert (log["reason"], log["qty_change"]) == ("RESERVE", -2)


def test_multiline_failure_rolls_back_all(client, db):
    before = counts(db)
    stock = db.execute("SELECT stock_qty FROM product_variant WHERE variant_id=1").fetchone()[
        "stock_qty"
    ]
    post(
        client,
        "/orders",
        {
            "member_id": 1,
            "address_id": 1,
            "items": [{"variant_id": 1, "quantity": 1}, {"variant_id": 2, "quantity": 10000}],
        },
        expected=409,
    )
    assert counts(db) == before
    assert (
        db.execute("SELECT stock_qty FROM product_variant WHERE variant_id=1").fetchone()[
            "stock_qty"
        ]
        == stock
    )


def test_foreign_address_rejected(client, db):
    before = counts(db)
    post(
        client,
        "/orders",
        {"member_id": 1, "address_id": 3, "items": [{"variant_id": 1, "quantity": 1}]},
        expected=422,
    )
    assert counts(db) == before


def test_cancel_once_and_payment_retry(client, db):
    stock = db.execute("SELECT stock_qty FROM product_variant WHERE variant_id=1").fetchone()[
        "stock_qty"
    ]
    order = new_order(client)
    oid = order["order_id"]
    post(client, f"/orders/{oid}/mock-pay", {"success": False})
    assert client.get(f"/orders/{oid}").json()["order_status"] == "PENDING_PAYMENT"
    post(client, f"/orders/{oid}/cancel", expected=200)
    post(client, f"/orders/{oid}/cancel", expected=409)
    post(client, f"/orders/{oid}/mock-pay", {"success": True}, expected=409)
    assert (
        db.execute("SELECT stock_qty FROM product_variant WHERE variant_id=1").fetchone()[
            "stock_qty"
        ]
        == stock
    )
    order2 = new_order(client)
    oid2 = order2["order_id"]
    post(client, f"/orders/{oid2}/mock-pay", {"success": False})
    post(client, f"/orders/{oid2}/mock-pay", {"success": True})
    post(client, f"/orders/{oid2}/mock-pay", {"success": True}, expected=409)
    post(client, f"/orders/{oid2}/cancel", expected=409)
    assert len(client.get(f"/orders/{oid2}").json()["payments"]) == 2


def test_shipping_review_and_product_aggregation(client, db):
    order = new_order(client)
    oid = order["order_id"]
    post(client, f"/orders/{oid}/shipment", {}, expected=409)
    post(client, "/products/1/reviews", {"member_id": 1, "rating": 5}, expected=409)
    shipment = deliver(client, order)
    assert shipment["recipient_name"] == "測試會員 1"
    assert "示範路" in shipment["shipping_address"]
    assert client.get(f"/orders/{oid}").json()["order_status"] == "DELIVERED"
    review = post(
        client, "/products/1/reviews", {"member_id": 1, "rating": 4, "content": "Delivered"}
    )
    assert review["product_id"] == 1
    post(client, "/products/1/reviews", {"member_id": 1, "rating": 5}, expected=409)
    report = client.get("/products/1/reviews").json()
    assert report["review_count"] >= 1
    # A different SKU of the same product still shares one member/product review.
    second = new_order(client, items=[{"variant_id": 2, "quantity": 1}])
    deliver(client, second)
    post(client, "/products/1/reviews", {"member_id": 1, "rating": 3}, expected=409)
    sid = shipment["shipment_id"]
    assert client.patch(f"/shipments/{sid}", json={"shipment_status": "SHIPPED"}).status_code == 409


def test_shipping_sequence_and_tracking_required(client):
    order = new_order(client)
    oid = order["order_id"]
    post(client, f"/orders/{oid}/mock-pay", {"success": True})
    shipment = post(client, f"/orders/{oid}/shipment", {})
    sid = shipment["shipment_id"]
    assert (
        client.patch(f"/shipments/{sid}", json={"shipment_status": "DELIVERED"}).status_code == 409
    )
    assert client.patch(f"/shipments/{sid}", json={"shipment_status": "SHIPPED"}).status_code == 409
    assert (
        client.patch(
            f"/shipments/{sid}", json={"shipment_status": "SHIPPED", "tracking_number": "ABC"}
        ).status_code
        == 200
    )
    response = client.patch(f"/shipments/{sid}", json={"shipment_status": "DELIVERED"})
    assert response.status_code == 200
    assert response.json()["shipped_at"] and response.json()["delivered_at"]


def test_inventory_adjustments_and_reconciliation(client, db):
    before = db.execute("SELECT stock_qty FROM product_variant WHERE variant_id=60").fetchone()[
        "stock_qty"
    ]
    post(client, "/variants/60/restock", {"quantity": 10}, expected=200)
    post(client, "/variants/60/adjust", {"quantity_change": -2}, expected=200)
    post(client, "/variants/60/adjust", {"quantity_change": -1000}, expected=409)
    assert (
        db.execute("SELECT stock_qty FROM product_variant WHERE variant_id=60").fetchone()[
            "stock_qty"
        ]
        == before + 8
    )
    assert (
        db.execute("""SELECT count(*) AS n FROM product_variant v WHERE stock_qty <>
        (SELECT coalesce(sum(qty_change),0) FROM inventory_log l WHERE l.variant_id=v.variant_id)""").fetchone()[
            "n"
        ]
        == 0
    )
    report = client.get("/reports/inventory?variant_id=60").json()
    assert report["logs"][0]["reason"] == "ADJUSTMENT"


def test_sales_excludes_unpaid_and_cancelled(client):
    original = next(r for r in client.get("/reports/sales").json() if r["product_id"] == 1)
    order = new_order(client)
    unpaid = next(r for r in client.get("/reports/sales").json() if r["product_id"] == 1)
    assert unpaid == original
    post(client, f"/orders/{order['order_id']}/mock-pay", {"success": True})
    paid = next(r for r in client.get("/reports/sales").json() if r["product_id"] == 1)
    assert paid["units_sold"] == original["units_sold"] + 2
    assert Decimal(str(paid["merchandise_revenue"])) == Decimal(
        str(original["merchandise_revenue"])
    ) + 2 * Decimal(str(order["items"][0]["unit_price"]))


@pytest.mark.parametrize(
    "payload",
    [
        {"member_id": 1, "address_id": 1, "items": []},
        {"member_id": 1, "address_id": 1, "items": [{"variant_id": 1, "quantity": 0}]},
        {
            "member_id": 1,
            "address_id": 1,
            "shipping_fee": -1,
            "items": [{"variant_id": 1, "quantity": 1}],
        },
        {
            "member_id": 1,
            "address_id": 1,
            "items": [{"variant_id": 1, "quantity": 1}, {"variant_id": 1, "quantity": 1}],
        },
        {
            "member_id": 1,
            "address_id": 1,
            "items": [{"variant_id": 1, "quantity": 1, "unit_price": 1}],
        },
    ],
)
def test_invalid_orders(client, payload):
    post(client, "/orders", payload, expected=422)


def test_database_constraints_and_deferred_commit(client, db, database):
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE product_variant SET stock_qty=-1 WHERE variant_id=1")
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO review(product_id,member_id,rating) VALUES (1,1,5)")
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE order_item SET quantity=2 WHERE order_item_id=1")
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE orders SET order_status='PAID' WHERE order_id=1")
    with pytest.raises(psycopg.errors.CheckViolation), database() as conn:
        conn.execute("INSERT INTO orders(member_id,address_id) VALUES (1,1)")
    # Deliberately trigger a deferred error during dependency cleanup and ensure HTTP 409.
    from app import main

    original = main.order_detail

    def corrupt_total(conn, oid):
        result = original(conn, oid)
        conn.execute("UPDATE orders SET total_amount=0 WHERE order_id=%s", (oid,))
        return result

    from unittest.mock import patch

    with patch.object(main, "order_detail", corrupt_total):
        post(
            client,
            "/orders",
            {"member_id": 1, "address_id": 1, "items": [{"variant_id": 1, "quantity": 1}]},
            expected=409,
        )


def test_last_item_concurrent_orders(client, db):
    # SKU 60 starts at 3 and has no seeded purchases.
    post(client, "/variants/60/adjust", {"quantity_change": -2}, expected=200)
    barrier = Barrier(2)

    def buy():
        with TestClient(app) as concurrent_client:
            barrier.wait(timeout=10)
            return concurrent_client.post(
                "/orders",
                json={
                    "member_id": 1,
                    "address_id": 1,
                    "items": [{"variant_id": 60, "quantity": 1}],
                },
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: buy(), range(2)))
    assert sorted(results) == [201, 409]
    assert (
        db.execute("SELECT stock_qty FROM product_variant WHERE variant_id=60").fetchone()[
            "stock_qty"
        ]
        == 0
    )


def test_payment_cancel_race(client, db):
    order = new_order(client)
    oid = order["order_id"]
    barrier = Barrier(2)

    def operate(action):
        with TestClient(app) as concurrent_client:
            barrier.wait(timeout=10)
            body = {"success": True} if action == "mock-pay" else None
            return concurrent_client.post(f"/orders/{oid}/{action}", json=body).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(operate, ["mock-pay", "cancel"]))
    assert 409 in results
    detail = client.get(f"/orders/{oid}").json()
    assert detail["order_status"] in ("PAID", "CANCELLED")
    logs = db.execute(
        "SELECT reason FROM inventory_log WHERE order_item_id=%s",
        (order["items"][0]["order_item_id"],),
    ).fetchall()
    assert len(logs) == (2 if detail["order_status"] == "CANCELLED" else 1)
