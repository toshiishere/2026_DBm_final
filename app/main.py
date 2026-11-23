import logging
import os
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg import sql

from app.db import get_db
from app.models import (
    AddressCreate,
    Adjustment,
    CategoryCreate,
    MemberCreate,
    MockPay,
    OrderCreate,
    ProductCreate,
    ProductPatch,
    Restock,
    ReviewCreate,
    ShipmentCreate,
    ShipmentPatch,
    VariantCreate,
)

app = FastAPI(
    title="Shoes & Apparel Database Demo",
    version="0.1.0",
    description="Classroom demo. Choose member_id directly; no login or authentication.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(
        ","
    ),
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type"],
)
# Commit before response transmission, so deferred DB constraints produce an HTTP error.
DB = Annotated[psycopg.Connection, Depends(get_db, scope="function")]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


@app.exception_handler(psycopg.Error)
async def database_error(request: Request, exc: psycopg.Error):
    if isinstance(exc, psycopg.errors.UniqueViolation):
        status, detail = 409, "A unique value already exists"
    elif isinstance(exc, psycopg.errors.ForeignKeyViolation):
        status, detail = 422, "Referenced record is missing or address does not belong to member"
    elif isinstance(exc, psycopg.errors.CheckViolation):
        status, detail = 409, exc.diag.message_primary or "Database rule rejected this operation"
    elif isinstance(
        exc, (psycopg.errors.NumericValueOutOfRange, psycopg.errors.StringDataRightTruncation)
    ):
        status, detail = 422, "Value exceeds database limits"
    elif isinstance(exc, (psycopg.OperationalError, psycopg.errors.DeadlockDetected)):
        status, detail = 503, "Database unavailable or transaction conflicted; retry the request"
    else:
        logging.getLogger(__name__).exception("Database operation failed")
        status, detail = 500, "Database operation failed"
    return JSONResponse(status_code=status, content={"detail": detail})


def required(row, name="Record"):
    if row is None:
        raise HTTPException(404, f"{name} not found")
    return row


def insert(conn, table, values):
    keys = list(values)
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
        sql.Identifier(table),
        sql.SQL(",").join(map(sql.Identifier, keys)),
        sql.SQL(",").join(sql.Placeholder() for _ in keys),
    )
    return conn.execute(query, [values[k] for k in keys]).fetchone()


def order_detail(conn, order_id):
    order = required(
        conn.execute("SELECT * FROM orders WHERE order_id=%s", (order_id,)).fetchone(), "Order"
    )
    order["items"] = conn.execute(
        """
        SELECT oi.*, p.product_id, p.product_name, v.sku, v.color, v.size,
               oi.quantity * oi.unit_price AS subtotal
        FROM order_item oi JOIN product_variant v USING (variant_id)
        JOIN product p USING (product_id) WHERE oi.order_id=%s ORDER BY oi.order_item_id
    """,
        (order_id,),
    ).fetchall()
    order["payments"] = conn.execute(
        "SELECT * FROM payment WHERE order_id=%s ORDER BY payment_id", (order_id,)
    ).fetchall()
    order["shipment"] = conn.execute(
        "SELECT * FROM shipment WHERE order_id=%s", (order_id,)
    ).fetchone()
    return order


@app.get("/health", tags=["System"])
def health(conn: DB):
    conn.execute("SELECT 1 FROM member LIMIT 1")
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index():
    return {"name": app.title, "docs": "/docs", "health": "/health"}


@app.get("/members", tags=["Members"])
def members(conn: DB, limit: Limit = 100, offset: Offset = 0):
    return conn.execute(
        "SELECT * FROM member ORDER BY member_id LIMIT %s OFFSET %s", (limit, offset)
    ).fetchall()


@app.post("/members", status_code=201, tags=["Members"])
def create_member(body: MemberCreate, conn: DB):
    return insert(conn, "member", body.model_dump())


@app.get("/members/{member_id}/addresses", tags=["Members"])
def addresses(member_id: int, conn: DB):
    required(
        conn.execute("SELECT member_id FROM member WHERE member_id=%s", (member_id,)).fetchone(),
        "Member",
    )
    return conn.execute(
        "SELECT * FROM member_address WHERE member_id=%s ORDER BY is_default DESC, address_id",
        (member_id,),
    ).fetchall()


@app.post("/members/{member_id}/addresses", status_code=201, tags=["Members"])
def create_address(member_id: int, body: AddressCreate, conn: DB):
    # Serialize default-address changes for a member.
    required(
        conn.execute(
            "SELECT member_id FROM member WHERE member_id=%s FOR UPDATE", (member_id,)
        ).fetchone(),
        "Member",
    )
    if body.is_default:
        conn.execute(
            "UPDATE member_address SET is_default=false WHERE member_id=%s AND is_default",
            (member_id,),
        )
    return insert(conn, "member_address", {"member_id": member_id, **body.model_dump()})


@app.get("/categories", tags=["Catalog"])
def categories(conn: DB):
    return conn.execute("SELECT * FROM category ORDER BY category_id").fetchall()


@app.post("/categories", status_code=201, tags=["Catalog"])
def create_category(body: CategoryCreate, conn: DB):
    return insert(conn, "category", body.model_dump())


@app.get("/products", tags=["Catalog"])
def products(
    conn: DB,
    category_id: int | None = None,
    q: str = "",
    active_only: bool = True,
    limit: Limit = 100,
    offset: Offset = 0,
):
    return conn.execute(
        """
        SELECT p.*, c.category_name, r.review_count, r.average_rating,
               coalesce((SELECT sum(stock_qty) FROM product_variant v WHERE v.product_id=p.product_id),0) AS stock_qty
        FROM product p JOIN category c USING (category_id)
        JOIN product_rating_summary r USING (product_id)
        WHERE (%s::bigint IS NULL OR p.category_id=%s)
          AND (NOT %s OR p.is_active)
          AND (p.product_name ILIKE %s OR p.description ILIKE %s)
        ORDER BY p.product_id LIMIT %s OFFSET %s
    """,
        (category_id, category_id, active_only, f"%{q}%", f"%{q}%", limit, offset),
    ).fetchall()


@app.get("/products/{product_id}", tags=["Catalog"])
def product_detail(product_id: int, conn: DB):
    product = required(
        conn.execute(
            """
        SELECT p.*, c.category_name, r.review_count, r.average_rating FROM product p
        JOIN category c USING (category_id) JOIN product_rating_summary r USING (product_id)
        WHERE p.product_id=%s
    """,
            (product_id,),
        ).fetchone(),
        "Product",
    )
    product["variants"] = conn.execute(
        "SELECT * FROM product_variant WHERE product_id=%s ORDER BY variant_id", (product_id,)
    ).fetchall()
    return product


def add_variant(conn, product_id, body):
    variant = insert(conn, "product_variant", {"product_id": product_id, **body.model_dump()})
    if variant["stock_qty"]:
        insert(
            conn,
            "inventory_log",
            {
                "variant_id": variant["variant_id"],
                "qty_change": variant["stock_qty"],
                "reason": "RESTOCK",
            },
        )
    return variant


@app.post("/products", status_code=201, tags=["Catalog"])
def create_product(body: ProductCreate, conn: DB):
    product = insert(conn, "product", body.model_dump(exclude={"variants"}))
    product["variants"] = [add_variant(conn, product["product_id"], v) for v in body.variants]
    return product


@app.patch("/products/{product_id}", tags=["Catalog"])
def patch_product(product_id: int, body: ProductPatch, conn: DB):
    values = body.model_dump(exclude_unset=True)
    query = sql.SQL("UPDATE product SET {} WHERE product_id=%s RETURNING *").format(
        sql.SQL(",").join(sql.SQL("{}=%s").format(sql.Identifier(k)) for k in values)
    )
    return required(conn.execute(query, [*values.values(), product_id]).fetchone(), "Product")


@app.post("/products/{product_id}/variants", status_code=201, tags=["Catalog"])
def create_variant(product_id: int, body: VariantCreate, conn: DB):
    required(
        conn.execute(
            "SELECT product_id FROM product WHERE product_id=%s", (product_id,)
        ).fetchone(),
        "Product",
    )
    return add_variant(conn, product_id, body)


@app.post("/orders", status_code=201, tags=["Orders"])
def create_order(body: OrderCreate, conn: DB):
    order = insert(conn, "orders", body.model_dump(exclude={"items"}))
    # Consistent SKU lock ordering prevents opposite-order baskets from deadlocking.
    for item in sorted(body.items, key=lambda i: i.variant_id):
        insert(
            conn,
            "order_item",
            {"order_id": order["order_id"], **item.model_dump(), "unit_price": 0},
        )
    return order_detail(conn, order["order_id"])


@app.get("/members/{member_id}/orders", tags=["Orders"])
def member_orders(member_id: int, conn: DB, limit: Limit = 100, offset: Offset = 0):
    required(
        conn.execute("SELECT member_id FROM member WHERE member_id=%s", (member_id,)).fetchone(),
        "Member",
    )
    return conn.execute(
        "SELECT * FROM orders WHERE member_id=%s ORDER BY order_date DESC, order_id DESC LIMIT %s OFFSET %s",
        (member_id, limit, offset),
    ).fetchall()


@app.get("/orders/{order_id}", tags=["Orders"])
def get_order(order_id: int, conn: DB):
    return order_detail(conn, order_id)


@app.post("/orders/{order_id}/mock-pay", status_code=201, tags=["Orders"])
def mock_pay(order_id: int, body: MockPay, conn: DB):
    order = required(
        conn.execute("SELECT * FROM orders WHERE order_id=%s FOR UPDATE", (order_id,)).fetchone(),
        "Order",
    )
    if order["order_status"] != "PENDING_PAYMENT":
        raise HTTPException(409, "Only pending orders accept payment attempts")
    return insert(
        conn,
        "payment",
        {
            "order_id": order_id,
            "payment_method": body.payment_method,
            "amount": order["total_amount"],
            "payment_status": "PAID" if body.success else "FAILED",
        },
    )


@app.post("/orders/{order_id}/cancel", tags=["Orders"])
def cancel_order(order_id: int, conn: DB):
    order = required(
        conn.execute("SELECT * FROM orders WHERE order_id=%s FOR UPDATE", (order_id,)).fetchone(),
        "Order",
    )
    if order["order_status"] != "PENDING_PAYMENT":
        raise HTTPException(409, "Only pending orders can be cancelled")
    conn.execute("UPDATE orders SET order_status='CANCELLED' WHERE order_id=%s", (order_id,))
    return order_detail(conn, order_id)


@app.post("/orders/{order_id}/shipment", status_code=201, tags=["Shipping"])
def create_shipment(order_id: int, body: ShipmentCreate, conn: DB):
    required(
        conn.execute(
            "SELECT order_id FROM orders WHERE order_id=%s FOR UPDATE", (order_id,)
        ).fetchone(),
        "Order",
    )
    # Recipient and address snapshot are filled by the database trigger.
    return insert(conn, "shipment", {"order_id": order_id, **body.model_dump()})


@app.patch("/shipments/{shipment_id}", tags=["Shipping"])
def patch_shipment(shipment_id: int, body: ShipmentPatch, conn: DB):
    # Match lock order in create_shipment: order first, then shipment.
    shipment = required(
        conn.execute(
            "SELECT order_id FROM shipment WHERE shipment_id=%s", (shipment_id,)
        ).fetchone(),
        "Shipment",
    )
    conn.execute(
        "SELECT order_id FROM orders WHERE order_id=%s FOR UPDATE", (shipment["order_id"],)
    )
    return required(
        conn.execute(
            """
        UPDATE shipment SET shipment_status=%s, tracking_number=coalesce(%s,tracking_number)
        WHERE shipment_id=%s RETURNING *
    """,
            (body.shipment_status, body.tracking_number, shipment_id),
        ).fetchone(),
        "Shipment",
    )


@app.get("/products/{product_id}/reviews", tags=["Reviews"])
def reviews(product_id: int, conn: DB, limit: Limit = 100, offset: Offset = 0):
    required(
        conn.execute(
            "SELECT product_id FROM product WHERE product_id=%s", (product_id,)
        ).fetchone(),
        "Product",
    )
    summary = conn.execute(
        "SELECT * FROM product_rating_summary WHERE product_id=%s", (product_id,)
    ).fetchone()
    summary["reviews"] = conn.execute(
        """
        SELECT r.*, m.member_name FROM review r JOIN member m USING (member_id)
        WHERE product_id=%s ORDER BY created_at DESC, review_id DESC LIMIT %s OFFSET %s
    """,
        (product_id, limit, offset),
    ).fetchall()
    return summary


@app.post("/products/{product_id}/reviews", status_code=201, tags=["Reviews"])
def create_review(product_id: int, body: ReviewCreate, conn: DB):
    return insert(conn, "review", {"product_id": product_id, **body.model_dump()})


def change_inventory(conn, variant_id, delta, reason):
    variant = conn.execute(
        """
        UPDATE product_variant SET stock_qty=stock_qty+%s
        WHERE variant_id=%s AND stock_qty+%s >= 0 RETURNING *
    """,
        (delta, variant_id, delta),
    ).fetchone()
    if variant is None:
        required(
            conn.execute(
                "SELECT variant_id FROM product_variant WHERE variant_id=%s", (variant_id,)
            ).fetchone(),
            "Variant",
        )
        raise HTTPException(409, "Adjustment would make stock negative")
    log = insert(
        conn, "inventory_log", {"variant_id": variant_id, "qty_change": delta, "reason": reason}
    )
    return {"variant": variant, "log": log}


@app.post("/variants/{variant_id}/restock", tags=["Inventory"])
def restock(variant_id: int, body: Restock, conn: DB):
    return change_inventory(conn, variant_id, body.quantity, "RESTOCK")


@app.post("/variants/{variant_id}/adjust", tags=["Inventory"])
def adjust(variant_id: int, body: Adjustment, conn: DB):
    return change_inventory(conn, variant_id, body.quantity_change, "ADJUSTMENT")


@app.get("/reports/sales", tags=["Reports"])
def sales(conn: DB):
    return conn.execute(
        "SELECT * FROM product_sales_summary ORDER BY merchandise_revenue DESC, product_id"
    ).fetchall()


@app.get("/reports/inventory", tags=["Reports"])
def inventory(
    conn: DB,
    threshold: Annotated[int, Query(ge=0)] = 5,
    variant_id: int | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
):
    low = conn.execute(
        """
        SELECT v.*, p.product_name FROM product_variant v JOIN product p USING (product_id)
        WHERE stock_qty < %s AND (%s::bigint IS NULL OR variant_id=%s)
        ORDER BY stock_qty, variant_id
    """,
        (threshold, variant_id, variant_id),
    ).fetchall()
    logs = conn.execute(
        """
        SELECT l.*, v.sku FROM inventory_log l JOIN product_variant v USING (variant_id)
        WHERE (%s::bigint IS NULL OR variant_id=%s)
        ORDER BY inventory_log_id DESC LIMIT %s OFFSET %s
    """,
        (variant_id, variant_id, limit, offset),
    ).fetchall()
    return {"threshold": threshold, "low_stock": low, "logs": logs}


# Use jsonable_encoder's standard Decimal -> JSON number behavior consistently.
# Monetary arithmetic and persistence always use Decimal / PostgreSQL numeric.
@app.get("/api-info", tags=["System"])
def api_info():
    return jsonable_encoder(
        {"currency": "TWD", "authentication": "none", "purpose": "classroom-demo"}
    )
