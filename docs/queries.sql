-- Run in psql with the demo database. Change this variable to switch members.
\set member_id 1

-- Member picker.
SELECT member_id, member_name, email FROM member ORDER BY member_id;

-- Multi-table order history.
SELECT o.order_id, o.order_status, m.member_name, p.product_name,
       v.sku, v.color, v.size, oi.quantity, oi.unit_price,
       oi.quantity * oi.unit_price AS subtotal, o.shipping_fee, o.total_amount
FROM orders o JOIN member m USING (member_id)
JOIN order_item oi USING (order_id)
JOIN product_variant v USING (variant_id) JOIN product p USING (product_id)
WHERE o.member_id = :member_id ORDER BY o.order_date DESC, oi.order_item_id;

-- Paid sales only; revenue excludes shipping.
SELECT * FROM product_sales_summary ORDER BY merchandise_revenue DESC;

-- Product-level reviews shared across sizes and colors.
SELECT p.product_name, r.review_count, r.average_rating
FROM product p JOIN product_rating_summary r USING (product_id)
ORDER BY r.average_rating DESC NULLS LAST;

-- Low stock.
SELECT p.product_name, v.sku, v.color, v.size, v.stock_qty
FROM product_variant v JOIN product p USING (product_id)
WHERE v.stock_qty < 5 ORDER BY v.stock_qty, v.variant_id;

-- Trace stock events to purchases and members, retaining manual adjustments.
SELECT l.inventory_log_id, v.sku, l.qty_change, l.reason, l.created_at,
       oi.order_id, o.member_id
FROM inventory_log l JOIN product_variant v USING (variant_id)
LEFT JOIN order_item oi USING (order_item_id) LEFT JOIN orders o USING (order_id)
ORDER BY l.inventory_log_id DESC LIMIT 30;

-- Reconciliation: zero rows means current stock matches initial stock + all logged changes.
SELECT v.sku, v.stock_qty, coalesce(sum(l.qty_change),0) AS logged_stock
FROM product_variant v LEFT JOIN inventory_log l USING (variant_id)
GROUP BY v.variant_id HAVING v.stock_qty <> coalesce(sum(l.qty_change),0);

-- Query-plan demonstration. A tiny seed may favor a sequential scan; that is normal.
EXPLAIN ANALYZE SELECT * FROM orders WHERE member_id = :member_id ORDER BY order_date DESC;

-- Inspect every column's type, nullability and default for the data dictionary.
SELECT table_name, column_name, data_type, is_nullable, column_default
FROM information_schema.columns WHERE table_schema = current_schema()
ORDER BY table_name, ordinal_position;
