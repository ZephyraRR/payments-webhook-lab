"""SQLite storage: every webhook event is stored once (idempotency by provider + event id) and payments move through states."""
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
"""


def connect(path: str = ":memory:") -> sqlite3.Connection:
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con
