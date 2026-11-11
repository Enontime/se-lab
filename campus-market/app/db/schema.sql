PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    username_key TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    account_type TEXT NOT NULL DEFAULT 'NORMAL'
        CHECK (account_type IN ('NORMAL', 'ADMIN')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    seller_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT,
    category TEXT,
    price_cents INTEGER CHECK (price_cents IS NULL OR price_cents >= 0),
    item_condition TEXT,
    description TEXT,
    trade_location TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'PENDING_REVIEW', 'REJECTED',
                          'ON_SALE', 'DELISTED', 'VIOLATION_DELISTED', 'SOLD')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT,
    violation_reason TEXT
);

CREATE TABLE IF NOT EXISTS reservations (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    buyer_id INTEGER NOT NULL REFERENCES users(id),
    request_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    -- v0.3: immutable buyer-observed facts for later historical display.
    expected_version INTEGER,
    expected_price_cents INTEGER,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'CONFIRMED', 'CANCELLED', 'COMPLETED')),
    created_at TEXT NOT NULL,
    confirmed_at TEXT,
    cancelled_at TEXT,
    completed_at TEXT,
    cancel_source TEXT CHECK (
        cancel_source IS NULL OR cancel_source IN
        ('BUYER', 'SELLER_REJECTED', 'PRODUCT_DELISTED', 'VIOLATION_DELISTED')
    ),
    cancel_reason TEXT,
    UNIQUE (buyer_id, request_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_reservation_per_product
ON reservations(product_id)
WHERE status IN ('PENDING', 'CONFIRMED');

CREATE TABLE IF NOT EXISTS product_reviews (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    admin_id INTEGER NOT NULL REFERENCES users(id),
    decision TEXT NOT NULL CHECK (decision IN ('APPROVED', 'REJECTED')),
    reason TEXT,
    reviewed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_logs (
    id INTEGER PRIMARY KEY,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('SUCCESS')),
    reason TEXT,
    created_at TEXT NOT NULL
);

-- API session infrastructure (not a domain entity in UML U3).
CREATE TABLE IF NOT EXISTS auth_sessions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_products_seller ON products(seller_id, id);
CREATE INDEX IF NOT EXISTS ix_products_catalog ON products(status, category, price_cents);
CREATE INDEX IF NOT EXISTS ix_products_published ON products(published_at DESC);
CREATE INDEX IF NOT EXISTS ix_reservations_buyer ON reservations(buyer_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_reservations_product ON reservations(product_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_logs_created ON operation_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS ix_reviews_product ON product_reviews(product_id, reviewed_at DESC);

-- v0.3 extension: each violation event is tracked independently of the product
-- state, so a previous administrator decision never authorizes a later incident.
CREATE TABLE IF NOT EXISTS violation_cases (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    flagged_by INTEGER NOT NULL REFERENCES users(id),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_by INTEGER REFERENCES users(id),
    resolved_at TEXT,
    handling_note TEXT,
    CHECK ((resolved_by IS NULL AND resolved_at IS NULL AND handling_note IS NULL)
        OR (resolved_by IS NOT NULL AND resolved_at IS NOT NULL AND handling_note IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_open_violation_case
    ON violation_cases(product_id) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_violation_product
    ON violation_cases(product_id, id DESC);
