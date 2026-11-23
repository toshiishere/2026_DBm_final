-- Runs once on an empty Docker volume. All people and transactions are synthetic.
BEGIN;
INSERT INTO category(category_name) VALUES ('Sneakers'), ('Tops'), ('Bottoms'), ('Outerwear');
INSERT INTO member(member_name, email, phone)
SELECT '測試會員 ' || n, 'member' || n || '@example.test', '0900' || lpad(n::text,6,'0')
FROM generate_series(1,12) n;
INSERT INTO member_address(member_id, recipient_name, phone, postal_code, city, district, street_address, is_default)
SELECT m.member_id, m.member_name, m.phone, '100', '臺北市', '中正區',
       '示範路 ' || m.member_id || ' 號 ' || a || ' 樓', a = 1
FROM member m CROSS JOIN generate_series(1,2) a ORDER BY m.member_id, a;
INSERT INTO product(category_id, product_name, description, price)
SELECT 1 + (n-1)/5,
    (ARRAY['Classic Sneaker','Runner Sneaker','Canvas Sneaker','Court Sneaker','Trail Sneaker',
           'Cotton Tee','Graphic Tee','Polo Shirt','Linen Shirt','Sport Top',
           'Straight Jeans','Chino Pants','Sport Shorts','Cargo Pants','Midi Skirt',
           'Denim Jacket','Windbreaker','Hoodie','Cardigan','Light Coat'])[n],
    '合成展示商品；固定售價，沒有折扣。', 390 + n * 80
FROM generate_series(1,20) n;
INSERT INTO product_variant(product_id, sku, color, size, stock_qty)
SELECT p.product_id, 'DEMO-' || lpad(p.product_id::text,3,'0') || '-' || s,
       CASE WHEN s = 3 THEN 'Black' ELSE 'White' END,
       CASE WHEN p.category_id = 1 THEN (39+s)::text ELSE (ARRAY['S','M','L'])[s] END,
       CASE WHEN p.product_id >= 19 THEN 3 ELSE 50 END
FROM product p CROSS JOIN generate_series(1,3) s ORDER BY p.product_id,s;
-- Initial RESTOCK baselines allow SUM(qty_change) to reconcile with current stock.
INSERT INTO inventory_log(variant_id, qty_change, reason)
SELECT variant_id, stock_qty, 'RESTOCK' FROM product_variant;
DO $$
DECLARE n integer; mid bigint; aid bigint; oid bigint; sid bigint; price_total numeric;
BEGIN
    FOR n IN 1..40 LOOP
        mid := 1 + (n-1)%12;
        SELECT address_id INTO aid FROM member_address WHERE member_id = mid AND is_default;
        INSERT INTO orders(member_id, address_id, shipping_fee) VALUES (mid,aid,60) RETURNING order_id INTO oid;
        INSERT INTO order_item(order_id, variant_id, quantity, unit_price)
            VALUES (oid, 1 + (n-1)%54, 1 + n%2, 0);
        IF n%5 = 0 THEN
            UPDATE orders SET order_status = 'CANCELLED' WHERE order_id = oid;
        ELSIF n%5 <> 1 THEN
            SELECT total_amount INTO price_total FROM orders WHERE order_id = oid;
            IF n%5 = 2 THEN
                INSERT INTO payment(order_id, payment_method, amount, payment_status)
                    VALUES (oid,'MOCK_CARD',price_total,'FAILED');
            END IF;
            INSERT INTO payment(order_id, payment_method, amount, payment_status)
                VALUES (oid,'MOCK_CARD',price_total,'PAID');
            IF n%5 IN (3,4) THEN
                INSERT INTO shipment(order_id,carrier) VALUES (oid,'Demo Logistics') RETURNING shipment_id INTO sid;
                UPDATE shipment SET shipment_status='SHIPPED', tracking_number='DEMO-' || oid WHERE shipment_id=sid;
                IF n%5 = 4 THEN
                    UPDATE shipment SET shipment_status='DELIVERED' WHERE shipment_id=sid;
                    INSERT INTO review(product_id,member_id,rating,content)
                        SELECT product_id,mid,5,'合成評價：商品已送達。' FROM product_variant WHERE variant_id=1+(n-1)%54
                        ON CONFLICT (member_id,product_id) DO NOTHING;
                END IF;
            END IF;
        END IF;
    END LOOP;
END $$;
COMMIT;
