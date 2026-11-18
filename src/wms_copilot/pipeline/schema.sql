-- Esquema WMS portable (SQLite en desarrollo, PostgreSQL en despliegue).
-- Todas las tablas usan claves TEXT (UUID generado en Python) para que el DDL
-- sea identico en ambos motores, sin SERIAL / IDENTITY especifico de motor.

CREATE TABLE IF NOT EXISTS products (
    product_id   INTEGER PRIMARY KEY,
    sku          TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL,
    unit_cost    REAL NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS warehouses (
    warehouse_id    INTEGER PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    city            TEXT NOT NULL,
    capacity_units  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_movements (
    movement_id    INTEGER PRIMARY KEY,
    movement_type  TEXT NOT NULL,          -- IN | OUT | ADJUST | TRANSFER
    product_id     INTEGER NOT NULL,
    warehouse_id   INTEGER NOT NULL,
    quantity       INTEGER NOT NULL,       -- siempre positivo; el signo lo da movement_type
    moved_at       TIMESTAMP NOT NULL,
    reference_doc  TEXT,
    source_system  TEXT NOT NULL           -- BOS | SAP | WMS
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id         INTEGER PRIMARY KEY,
    po_number     TEXT NOT NULL UNIQUE,
    product_id    INTEGER NOT NULL,
    warehouse_id  INTEGER NOT NULL,
    qty_ordered   INTEGER NOT NULL,
    qty_received  INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL,           -- OPEN | PARTIAL | RECEIVED | CANCELLED
    ordered_at    TIMESTAMP NOT NULL,
    source_system TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS etl_runs (
    run_id          TEXT PRIMARY KEY,
    dag_id          TEXT NOT NULL,
    task_id         TEXT,
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    status          TEXT NOT NULL,         -- RUNNING | SUCCESS | FAILED
    rows_processed  INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT
);

CREATE TABLE IF NOT EXISTS data_quality_issues (
    issue_id    TEXT PRIMARY KEY,
    detected_at TIMESTAMP NOT NULL,
    rule_code   TEXT NOT NULL,
    severity    TEXT NOT NULL,             -- HIGH | MEDIUM | LOW
    entity_key  TEXT NOT NULL,             -- clave natural del hallazgo (evita duplicados)
    detail      TEXT NOT NULL,
    resolved    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS kb_chunks (
    chunk_id    TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL,
    title       TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text        TEXT NOT NULL,
    embedding   TEXT NOT NULL,             -- vector serializado en JSON
    updated_at  TIMESTAMP NOT NULL,
    UNIQUE (doc_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS llm_calls (
    call_id     TEXT PRIMARY KEY,
    request_id  TEXT,
    provider    TEXT NOT NULL,
    model       TEXT NOT NULL,
    task        TEXT NOT NULL,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    cost_usd    REAL NOT NULL DEFAULT 0,
    latency_ms  INTEGER NOT NULL DEFAULT 0,
    ok          INTEGER NOT NULL DEFAULT 1,
    created_at  TIMESTAMP NOT NULL
);

DROP VIEW IF EXISTS v_inventory_balance;
CREATE VIEW v_inventory_balance AS
SELECT
    p.sku                         AS sku,
    p.name                        AS product_name,
    p.category                    AS category,
    w.code                        AS warehouse_code,
    SUM(CASE m.movement_type
            WHEN 'IN'       THEN  m.quantity
            WHEN 'OUT'      THEN -m.quantity
            WHEN 'ADJUST'   THEN  m.quantity
            WHEN 'TRANSFER' THEN -m.quantity
            ELSE 0
        END)                      AS on_hand
FROM inventory_movements m
JOIN products p   ON p.product_id   = m.product_id
JOIN warehouses w ON w.warehouse_id = m.warehouse_id
GROUP BY p.sku, p.name, p.category, w.code;

DROP VIEW IF EXISTS v_purchase_order_status;
CREATE VIEW v_purchase_order_status AS
SELECT
    po.po_number      AS po_number,
    po.status         AS status,
    p.sku             AS sku,
    w.code            AS warehouse_code,
    po.qty_ordered    AS qty_ordered,
    po.qty_received   AS qty_received,
    (po.qty_received - po.qty_ordered) AS received_delta
FROM purchase_orders po
JOIN products p   ON p.product_id   = po.product_id
JOIN warehouses w ON w.warehouse_id = po.warehouse_id;
