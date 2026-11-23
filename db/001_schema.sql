BEGIN;
CREATE TABLE member (
    member_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    member_name varchar(100) NOT NULL CHECK (btrim(member_name) <> ''),
    email varchar(254) NOT NULL UNIQUE CHECK (position('@' in email) > 1),
    phone varchar(30) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE member_address (
    address_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    member_id bigint NOT NULL REFERENCES member,
    recipient_name varchar(100) NOT NULL,
    phone varchar(30) NOT NULL,
    postal_code varchar(20) NOT NULL,
    city varchar(100) NOT NULL,
    district varchar(100) NOT NULL,
    street_address text NOT NULL,
    is_default boolean NOT NULL DEFAULT false,
    UNIQUE (address_id, member_id)
);
CREATE UNIQUE INDEX one_default_address ON member_address(member_id) WHERE is_default;
CREATE TABLE category (
    category_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    category_name varchar(100) NOT NULL UNIQUE CHECK (btrim(category_name) <> '')
);
CREATE TABLE product (
    product_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    category_id bigint NOT NULL REFERENCES category,
    product_name varchar(200) NOT NULL CHECK (btrim(product_name) <> ''),
    description text NOT NULL DEFAULT '',
    price numeric(12,2) NOT NULL CHECK (price >= 0),
    image_url text,
    is_active boolean NOT NULL DEFAULT true
);
CREATE TABLE product_variant (
    variant_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id bigint NOT NULL REFERENCES product,
    sku varchar(100) NOT NULL UNIQUE CHECK (btrim(sku) <> ''),
    color varchar(50) NOT NULL,
    size varchar(30) NOT NULL,
    stock_qty integer NOT NULL DEFAULT 0 CHECK (stock_qty >= 0),
    UNIQUE (product_id, color, size)
);
CREATE TABLE orders (
    order_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    member_id bigint NOT NULL REFERENCES member,
    address_id bigint NOT NULL,
    order_date timestamptz NOT NULL DEFAULT now(),
    order_status varchar(30) NOT NULL DEFAULT 'PENDING_PAYMENT'
        CHECK (order_status IN ('PENDING_PAYMENT','PAID','PREPARING','SHIPPED','DELIVERED','CANCELLED')),
    shipping_fee numeric(12,2) NOT NULL DEFAULT 60 CHECK (shipping_fee >= 0),
    total_amount numeric(14,2) NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
    FOREIGN KEY (address_id, member_id) REFERENCES member_address(address_id, member_id)
);
CREATE TABLE order_item (
    order_item_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES orders,
    variant_id bigint NOT NULL REFERENCES product_variant,
    quantity integer NOT NULL CHECK (quantity > 0),
    unit_price numeric(12,2) NOT NULL CHECK (unit_price >= 0),
    UNIQUE (order_id, variant_id)
);
CREATE TABLE payment (
    payment_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES orders,
    payment_method varchar(30) NOT NULL CHECK (payment_method IN ('MOCK_CARD','MOCK_TRANSFER')),
    amount numeric(14,2) NOT NULL CHECK (amount >= 0),
    payment_status varchar(20) NOT NULL CHECK (payment_status IN ('PAID','FAILED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    paid_at timestamptz,
    CHECK ((payment_status = 'PAID') = (paid_at IS NOT NULL))
);
CREATE UNIQUE INDEX one_paid_payment ON payment(order_id) WHERE payment_status = 'PAID';
CREATE TABLE shipment (
    shipment_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id bigint NOT NULL UNIQUE REFERENCES orders,
    recipient_name varchar(100) NOT NULL,
    phone varchar(30) NOT NULL,
    shipping_address text NOT NULL,
    carrier varchar(100) NOT NULL,
    tracking_number varchar(100),
    shipment_status varchar(20) NOT NULL DEFAULT 'PREPARING'
        CHECK (shipment_status IN ('PREPARING','SHIPPED','DELIVERED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    shipped_at timestamptz,
    delivered_at timestamptz,
    CHECK (shipment_status = 'PREPARING' OR (tracking_number IS NOT NULL AND btrim(tracking_number) <> ''))
);
CREATE TABLE review (
    review_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id bigint NOT NULL REFERENCES product,
    member_id bigint NOT NULL REFERENCES member,
    rating smallint NOT NULL CHECK (rating BETWEEN 1 AND 5),
    content text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (member_id, product_id)
);
CREATE TABLE inventory_log (
    inventory_log_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    variant_id bigint NOT NULL REFERENCES product_variant,
    order_item_id bigint REFERENCES order_item,
    qty_change integer NOT NULL CHECK (qty_change <> 0),
    reason varchar(20) NOT NULL CHECK (reason IN ('RESERVE','RELEASE','RESTOCK','ADJUSTMENT')),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((reason IN ('RESERVE','RELEASE')) = (order_item_id IS NOT NULL)),
    CHECK (reason <> 'RESERVE' OR qty_change < 0),
    CHECK (reason NOT IN ('RELEASE','RESTOCK') OR qty_change > 0)
);
CREATE UNIQUE INDEX one_item_inventory_event ON inventory_log(order_item_id, reason)
    WHERE order_item_id IS NOT NULL;
CREATE INDEX addresses_member_idx ON member_address(member_id);
CREATE INDEX products_category_idx ON product(category_id);
CREATE INDEX orders_member_date_idx ON orders(member_id, order_date DESC);
CREATE INDEX order_items_variant_idx ON order_item(variant_id);
CREATE INDEX payments_order_idx ON payment(order_id);
CREATE INDEX reviews_product_idx ON review(product_id);
CREATE INDEX stock_low_idx ON product_variant(stock_qty);
CREATE INDEX inventory_variant_date_idx ON inventory_log(variant_id, created_at DESC);

-- The database owns price snapshots and inventory reservation, even for direct SQL inserts.
CREATE FUNCTION reserve_order_item() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE current_status text; current_price numeric;
BEGIN
    SELECT order_status INTO current_status FROM orders WHERE order_id = NEW.order_id FOR UPDATE;
    IF current_status IS DISTINCT FROM 'PENDING_PAYMENT' THEN
        RAISE EXCEPTION 'Items can only be added to pending orders' USING ERRCODE = '23514';
    END IF;
    IF NEW.quantity <= 0 THEN
        RAISE EXCEPTION 'Quantity must be positive' USING ERRCODE = '23514';
    END IF;
    -- Lock the product while reading its price so concurrent price edits have a defined order.
    SELECT p.price INTO current_price FROM product p JOIN product_variant v USING (product_id)
        WHERE v.variant_id = NEW.variant_id AND p.is_active FOR SHARE OF p;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Variant does not exist or product is inactive' USING ERRCODE = '23514';
    END IF;
    UPDATE product_variant SET stock_qty = stock_qty - NEW.quantity
        WHERE variant_id = NEW.variant_id AND stock_qty >= NEW.quantity;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Insufficient stock' USING ERRCODE = '23514';
    END IF;
    NEW.unit_price := current_price;
    RETURN NEW;
END $$;
CREATE TRIGGER reserve_item BEFORE INSERT ON order_item FOR EACH ROW EXECUTE FUNCTION reserve_order_item();
CREATE FUNCTION record_order_item() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO inventory_log(variant_id, order_item_id, qty_change, reason)
        VALUES (NEW.variant_id, NEW.order_item_id, -NEW.quantity, 'RESERVE');
    UPDATE orders SET total_amount = shipping_fee +
        (SELECT sum(quantity * unit_price) FROM order_item WHERE order_id = NEW.order_id)
        WHERE order_id = NEW.order_id;
    RETURN NEW;
END $$;
CREATE TRIGGER record_item AFTER INSERT ON order_item FOR EACH ROW EXECUTE FUNCTION record_order_item();
CREATE FUNCTION reject_history_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Transaction history is immutable; use the documented workflow' USING ERRCODE = '23514';
END $$;
CREATE TRIGGER immutable_items BEFORE UPDATE OR DELETE ON order_item FOR EACH ROW EXECUTE FUNCTION reject_history_change();
CREATE TRIGGER immutable_payments BEFORE UPDATE OR DELETE ON payment FOR EACH ROW EXECUTE FUNCTION reject_history_change();
CREATE TRIGGER immutable_inventory BEFORE UPDATE OR DELETE ON inventory_log FOR EACH ROW EXECUTE FUNCTION reject_history_change();

CREATE FUNCTION validate_order_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item record;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.order_status <> 'PENDING_PAYMENT' THEN
            RAISE EXCEPTION 'New orders must await payment' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF (NEW.member_id, NEW.address_id, NEW.shipping_fee) IS DISTINCT FROM
       (OLD.member_id, OLD.address_id, OLD.shipping_fee) THEN
        RAISE EXCEPTION 'Order member, address and shipping fee are immutable' USING ERRCODE = '23514';
    END IF;
    IF NEW.order_status = OLD.order_status THEN RETURN NEW; END IF;
    IF OLD.order_status = 'PENDING_PAYMENT' AND NEW.order_status = 'CANCELLED' THEN
        FOR item IN SELECT * FROM order_item WHERE order_id = OLD.order_id ORDER BY variant_id LOOP
            UPDATE product_variant SET stock_qty = stock_qty + item.quantity WHERE variant_id = item.variant_id;
            INSERT INTO inventory_log(variant_id, order_item_id, qty_change, reason)
                VALUES (item.variant_id, item.order_item_id, item.quantity, 'RELEASE');
        END LOOP;
    ELSIF OLD.order_status = 'PENDING_PAYMENT' AND NEW.order_status = 'PAID' AND EXISTS
        (SELECT 1 FROM payment WHERE order_id = OLD.order_id AND payment_status = 'PAID') THEN NULL;
    ELSIF (OLD.order_status, NEW.order_status) IN
        (('PAID','PREPARING'), ('PREPARING','SHIPPED'), ('SHIPPED','DELIVERED')) AND EXISTS
        (SELECT 1 FROM shipment WHERE order_id = OLD.order_id AND shipment_status = NEW.order_status) THEN NULL;
    ELSE
        RAISE EXCEPTION 'Invalid order status transition' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER order_change BEFORE INSERT OR UPDATE ON orders FOR EACH ROW EXECUTE FUNCTION validate_order_change();
-- Deferred validation permits header + items to be constructed in one transaction, never an empty order.
CREATE FUNCTION validate_order_total() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE actual numeric; expected numeric; item_count integer;
BEGIN
    SELECT total_amount, shipping_fee INTO actual, expected FROM orders WHERE order_id = NEW.order_id;
    SELECT count(*), expected + coalesce(sum(quantity * unit_price),0)
        INTO item_count, expected FROM order_item WHERE order_id = NEW.order_id;
    IF item_count = 0 OR actual <> expected THEN
        RAISE EXCEPTION 'Order must contain items and its total must match their sum plus shipping' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER valid_order_total AFTER INSERT OR UPDATE ON orders
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_order_total();

CREATE FUNCTION validate_payment() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target orders%ROWTYPE;
BEGIN
    SELECT * INTO target FROM orders WHERE order_id = NEW.order_id FOR UPDATE;
    IF NOT FOUND OR target.order_status <> 'PENDING_PAYMENT' THEN
        RAISE EXCEPTION 'Only pending orders accept payment attempts' USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM order_item WHERE order_id = NEW.order_id) THEN
        RAISE EXCEPTION 'Empty orders cannot be paid' USING ERRCODE = '23514';
    END IF;
    IF NEW.amount <> target.total_amount THEN
        RAISE EXCEPTION 'Payment amount must equal order total' USING ERRCODE = '23514';
    END IF;
    NEW.paid_at := CASE WHEN NEW.payment_status = 'PAID' THEN now() ELSE NULL END;
    RETURN NEW;
END $$;
CREATE TRIGGER payment_check BEFORE INSERT ON payment FOR EACH ROW EXECUTE FUNCTION validate_payment();
CREATE FUNCTION apply_payment() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.payment_status = 'PAID' THEN UPDATE orders SET order_status = 'PAID' WHERE order_id = NEW.order_id; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER payment_apply AFTER INSERT ON payment FOR EACH ROW EXECUTE FUNCTION apply_payment();

CREATE FUNCTION validate_shipment() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target orders%ROWTYPE; address member_address%ROWTYPE;
BEGIN
    SELECT * INTO target FROM orders WHERE order_id = NEW.order_id FOR UPDATE;
    IF TG_OP = 'INSERT' THEN
        IF target.order_status IS DISTINCT FROM 'PAID' OR NEW.shipment_status <> 'PREPARING' THEN
            RAISE EXCEPTION 'Shipment requires a paid order and begins at PREPARING' USING ERRCODE = '23514';
        END IF;
        SELECT * INTO address FROM member_address WHERE address_id = target.address_id;
        NEW.recipient_name := address.recipient_name;
        NEW.phone := address.phone;
        NEW.shipping_address := concat_ws(' ', address.postal_code, address.city, address.district, address.street_address);
        NEW.shipped_at := NULL; NEW.delivered_at := NULL;
    ELSE
        IF (NEW.order_id, NEW.recipient_name, NEW.phone, NEW.shipping_address) IS DISTINCT FROM
           (OLD.order_id, OLD.recipient_name, OLD.phone, OLD.shipping_address) THEN
            RAISE EXCEPTION 'Shipment order and address snapshot are immutable' USING ERRCODE = '23514';
        END IF;
        IF (OLD.shipment_status, NEW.shipment_status) NOT IN
           (('PREPARING','SHIPPED'), ('SHIPPED','DELIVERED')) THEN
            RAISE EXCEPTION 'Shipment must proceed PREPARING -> SHIPPED -> DELIVERED' USING ERRCODE = '23514';
        END IF;
        NEW.shipped_at := CASE WHEN NEW.shipment_status = 'SHIPPED' THEN now() ELSE OLD.shipped_at END;
        NEW.delivered_at := CASE WHEN NEW.shipment_status = 'DELIVERED' THEN now() ELSE NULL END;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER shipment_check BEFORE INSERT OR UPDATE ON shipment FOR EACH ROW EXECUTE FUNCTION validate_shipment();
CREATE TRIGGER immutable_shipment_delete BEFORE DELETE ON shipment FOR EACH ROW EXECUTE FUNCTION reject_history_change();
CREATE FUNCTION apply_shipment() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE orders SET order_status = NEW.shipment_status WHERE order_id = NEW.order_id;
    RETURN NEW;
END $$;
CREATE TRIGGER shipment_apply AFTER INSERT OR UPDATE ON shipment FOR EACH ROW EXECUTE FUNCTION apply_shipment();
CREATE FUNCTION validate_review() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM orders o JOIN order_item oi USING (order_id)
        JOIN product_variant v USING (variant_id)
        WHERE o.member_id = NEW.member_id AND v.product_id = NEW.product_id AND o.order_status = 'DELIVERED'
    ) THEN
        RAISE EXCEPTION 'Review requires a delivered purchase of this product' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER review_eligibility BEFORE INSERT OR UPDATE ON review FOR EACH ROW EXECUTE FUNCTION validate_review();
CREATE VIEW product_sales_summary AS
SELECT p.product_id, p.product_name,
       coalesce(sum(oi.quantity) FILTER (WHERE o.order_status IN ('PAID','PREPARING','SHIPPED','DELIVERED')),0)::bigint AS units_sold,
       coalesce(sum(oi.quantity * oi.unit_price) FILTER (WHERE o.order_status IN ('PAID','PREPARING','SHIPPED','DELIVERED')),0) AS merchandise_revenue
FROM product p LEFT JOIN product_variant v USING (product_id)
LEFT JOIN order_item oi USING (variant_id) LEFT JOIN orders o USING (order_id)
GROUP BY p.product_id;
CREATE VIEW product_rating_summary AS
SELECT p.product_id, count(r.review_id) AS review_count, round(avg(r.rating),2) AS average_rating
FROM product p LEFT JOIN review r USING (product_id) GROUP BY p.product_id;
COMMIT;
