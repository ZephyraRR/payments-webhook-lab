"""SQLite storage.

events     every webhook event, stored once per provider (idempotency)
payments   one row per payment with its current state
pending    refunds and disputes that arrived BEFORE their payment (providers do not guarantee ordering)
anomalies  things a human should look at (refund larger than the payment, repeated payment event with another amount)
"""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    provider TEXT NOT NULL, event_id TEXT NOT NULL, type TEXT NOT NULL, received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (provider, event_id)
);
CREATE TABLE IF NOT EXISTS payments (
    provider TEXT NOT NULL, payment_id TEXT NOT NULL, order_id TEXT, amount INTEGER NOT NULL, currency TEXT NOT NULL,
    status TEXT NOT NULL, refunded INTEGER NOT NULL DEFAULT 0, disputed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (provider, payment_id)
);
CREATE TABLE IF NOT EXISTS pending (
    provider TEXT NOT NULL, payment_id TEXT NOT NULL, event_id TEXT NOT NULL, kind TEXT NOT NULL, amount INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (provider, event_id)
);
CREATE TABLE IF NOT EXISTS anomalies (
    id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, payment_id TEXT NOT NULL, note TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(path: str = ":memory:") -> sqlite3.Connection:
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con
