# Frontend integration

Base URL: `http://localhost:8000`. There is no `/api` prefix or authentication.
All bodies and responses use JSON. Send `Content-Type: application/json` for writes.
The selected member is provided in the URL or body, never a token or session.

## Conventions

- Successful reads and updates: HTTP `200`; resource creation: `201`.
- Missing resource: `404`; invalid request / reference: `422`; duplicate value or rejected business
  operation: `409`; database availability/transaction conflict: `503`.
- Errors: `{"detail": "message"}`. FastAPI request validation uses an array in `detail`.
- Money is TWD. JSON responses contain numeric values; request amounts may be numbers or decimal strings.
  All arithmetic is PostgreSQL `numeric` / Python `Decimal`; clients never submit a line-item price or total.
- Dates are ISO 8601 timestamps with timezone offsets.
- Paginated endpoints accept `limit` (default 100, maximum 200) and `offset` (default 0).
  Lists are JSON arrays; `/products/{id}/reviews` and `/reports/inventory` wrap arrays with summary data.
- Product search uses case-insensitive `q`; SQL LIKE wildcards `%` and `_` act as search wildcards.
- `POST /orders` rejects repeated `variant_id` entries; combine their quantities before submitting.
- Historical order items, payment attempts, and inventory logs cannot be edited or deleted.

## Routes

| Method | Route | Body / query |
|---|---|---|
| GET | `/members` | `limit`, `offset` |
| POST | `/members` | `member_name`, `email`, `phone` |
| GET | `/members/{member_id}/addresses` | — |
| POST | `/members/{member_id}/addresses` | `recipient_name`, `phone`, `postal_code`, `city`, `district`, `street_address`, `is_default=false` |
| GET | `/categories` | — |
| POST | `/categories` | `category_name` |
| GET | `/products` | `category_id`, `q`, `active_only=true`, `limit`, `offset` |
| GET | `/products/{product_id}` | Product with `variants`, `review_count`, `average_rating` |
| POST | `/products` | Product fields and at least one variant; example below |
| PATCH | `/products/{product_id}` | Any product fields; only `image_url` accepts null |
| POST | `/products/{product_id}/variants` | `sku`, `color`, `size`, `stock_qty=0` |
| POST | `/orders` | `member_id`, `address_id`, `shipping_fee=60`, `items` |
| GET | `/members/{member_id}/orders` | `limit`, `offset` |
| GET | `/orders/{order_id}` | Header with `items`, `payments`, `shipment` (nullable) |
| POST | `/orders/{order_id}/mock-pay` | `success=true`, `payment_method=MOCK_CARD` (`MOCK_TRANSFER` also allowed) |
| POST | `/orders/{order_id}/cancel` | No body; pending orders only |
| POST | `/orders/{order_id}/shipment` | `carrier="Demo Logistics"`, optional `tracking_number` |
| PATCH | `/shipments/{shipment_id}` | `shipment_status`, optional `tracking_number` |
| GET | `/products/{product_id}/reviews` | `limit`, `offset`; summary with `reviews` array |
| POST | `/products/{product_id}/reviews` | `member_id`, `rating` (1–5), `content=""` |
| POST | `/variants/{variant_id}/restock` | `quantity` (positive); returns `variant` and `log` |
| POST | `/variants/{variant_id}/adjust` | `quantity_change` (nonzero signed integer); returns `variant` and `log` |
| GET | `/reports/sales` | Product totals excluding pending/cancelled orders and shipping fees |
| GET | `/reports/inventory` | `threshold=5`, optional `variant_id`, `limit`, `offset` for logs |

Setting a newly added address as default clears the previous default atomically.
Product creation accepts `category_id`, `product_name`, `description=""`, `price`, `image_url=null`,
`is_active=true`, and `variants`. Deactivate products with PATCH rather than deleting purchase history.
New SKU stock is accompanied by an initial RESTOCK log. Existing stock changes use the stock endpoints.

```json
{
  "category_id": 1,
  "product_name": "Demo Sneaker",
  "description": "New classroom sample",
  "price": "1280.00",
  "variants": [
    {"sku": "NEW-WHITE-41", "color": "White", "size": "41", "stock_qty": 10},
    {"sku": "NEW-BLACK-42", "color": "Black", "size": "42", "stock_qty": 5}
  ]
}
```

## Browser purchase example

Run from an allowed frontend origin. Use IDs from API responses rather than hardcoding production data.
This example uses the documented seed IDs. Save the returned IDs when implementing your UI.

```javascript
const base = 'http://localhost:8000';
async function api(path, method = 'GET', body) {
  const response = await fetch(base + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(JSON.stringify(data.detail));
  return data;
}

const members = await api('/members');
const member = members[0]; // Replace with the dropdown's selected member.
const addresses = await api(`/members/${member.member_id}/addresses`);
const product = await api('/products/1');
const variant = product.variants.find(v => v.stock_qty > 0);
const order = await api('/orders', 'POST', {
  member_id: member.member_id,
  address_id: addresses[0].address_id,
  shipping_fee: 60,
  items: [{ variant_id: variant.variant_id, quantity: 1 }],
});

// A failed attempt remains recorded and the order stays PENDING_PAYMENT.
await api(`/orders/${order.order_id}/mock-pay`, 'POST', { success: false });
await api(`/orders/${order.order_id}/mock-pay`, 'POST', { success: true });
const shipment = await api(`/orders/${order.order_id}/shipment`, 'POST', {
  carrier: 'Demo Logistics',
});
await api(`/shipments/${shipment.shipment_id}`, 'PATCH', {
  shipment_status: 'SHIPPED', tracking_number: `DEMO-${order.order_id}`,
});
await api(`/shipments/${shipment.shipment_id}`, 'PATCH', {
  shipment_status: 'DELIVERED',
});
await api('/products/1/reviews', 'POST', {
  member_id: member.member_id, rating: 5, content: 'Delivered classroom demo',
});
console.log(await api(`/orders/${order.order_id}`));
```

Each member can review a product once. Repeating the example with the same member/product returns
409 at review creation. Buy another product or select another member to demonstrate another review.

Order transitions: `PENDING_PAYMENT → PAID → PREPARING → SHIPPED → DELIVERED`.
Cancellation is only `PENDING_PAYMENT → CANCELLED`. Shipment creation sets PREPARING; update it to
SHIPPED with a nonempty tracking number, then DELIVERED. Repeating or skipping a transition returns 409.
Failed payments keep stock reserved until the pending order is paid or explicitly cancelled.

On errors, refresh the order and product details before updating UI state. A 409 stock error leaves
no partial order or inventory changes. There is no automatic order expiration or payment integration.
