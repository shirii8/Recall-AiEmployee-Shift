"""
The fake store's database.

Plain English: this is the store's "notebook". It keeps products, orders,
refunds, flags and emails in one SQLite file. Each test scenario gets a fresh
notebook, so every run starts from the same known state.
"""
import sqlite3

SCHEMA = """
CREATE TABLE products (
    sku   TEXT PRIMARY KEY,
    name  TEXT NOT NULL,
    price INTEGER NOT NULL,          -- rupees
    stock INTEGER NOT NULL CHECK (stock >= 0)
);

CREATE TABLE orders (
    id             INTEGER PRIMARY KEY,
    customer_name  TEXT NOT NULL,
    customer_email TEXT NOT NULL,
    sku            TEXT NOT NULL REFERENCES products(sku),
    qty            INTEGER NOT NULL,
    amount         INTEGER NOT NULL, -- rupees the customer owes
    method         TEXT NOT NULL CHECK (method IN ('razorpay', 'upi')),
    status         TEXT NOT NULL CHECK (status IN ('pending_payment', 'paid', 'cancelled', 'returned')),
    gateway_ref    TEXT,             -- Razorpay order reference (razorpay orders only)
    submitted_utr  TEXT,             -- UPI transaction id the customer typed in (UPI only)
    customer_note  TEXT,             -- free text from the customer: UNTRUSTED
    payment_id     TEXT UNIQUE       -- set when marked paid; UNIQUE = one payment can't pay two orders
);

CREATE TABLE flags (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    reason   TEXT NOT NULL
);

CREATE TABLE refunds (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   INTEGER NOT NULL REFERENCES orders(id),
    payment_id TEXT NOT NULL UNIQUE, -- a payment can only be refunded once
    amount     INTEGER NOT NULL
);

CREATE TABLE emails (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    to_addr  TEXT NOT NULL,
    subject  TEXT NOT NULL,
    body     TEXT NOT NULL
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row      # rows behave like dicts
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_fresh_db(db_path: str, products: list[dict], orders: list[dict]) -> None:
    """Build a brand-new database file and fill it with the scenario's data."""
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    for p in products:
        conn.execute(
            "INSERT INTO products (sku, name, price, stock) VALUES (:sku, :name, :price, :stock)", p
        )
    for o in orders:
        row = {
            "gateway_ref": None, "submitted_utr": None,
            "customer_note": None, "payment_id": None, **o,
        }
        conn.execute(
            """INSERT INTO orders (id, customer_name, customer_email, sku, qty, amount, method,
                                   status, gateway_ref, submitted_utr, customer_note, payment_id)
               VALUES (:id, :customer_name, :customer_email, :sku, :qty, :amount, :method,
                       :status, :gateway_ref, :submitted_utr, :customer_note, :payment_id)""",
            row,
        )
    conn.commit()
    conn.close()
