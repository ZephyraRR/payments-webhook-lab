"""Business logic for payment events.

A repeated event id is ignored (providers retry), refunds and disputes accumulate on the payment.
"""
from sqlite3 import Connection


def record_event(con: Connection, provider: str, event_id: str, etype: str) -> bool:
    """True if the event is new, False if it was already processed."""
    cur = con.execute("INSERT OR IGNORE INTO events(provider, event_id, type) VALUES (?, ?, ?)", (provider, event_id, etype))
    con.commit()
    return cur.rowcount == 1


def apply_event(con: Connection, provider: str, event: dict) -> str:
    etype = event["type"]
    obj = event["data"]
    if not record_event(con, provider, event["id"], etype):
        return "duplicate"
    if etype in ("payment.succeeded", "PAYMENT.CAPTURE.COMPLETED"):
        con.execute(
            "INSERT OR REPLACE INTO payments(provider, payment_id, order_id, amount, currency, status) VALUES (?, ?, ?, ?, ?, 'paid')",
            (provider, obj["payment_id"], obj.get("order_id"), int(obj["amount"]), obj["currency"].upper()),
        )
    elif etype in ("payment.refunded", "PAYMENT.CAPTURE.REFUNDED"):
        con.execute(
            "UPDATE payments SET refunded = refunded + ?, "
            "status = CASE WHEN refunded + ? >= amount THEN 'refunded' ELSE 'partially_refunded' END "
            "WHERE provider = ? AND payment_id = ?",
            (int(obj["amount"]), int(obj["amount"]), provider, obj["payment_id"]),
        )
    elif etype in ("dispute.created", "CUSTOMER.DISPUTE.CREATED"):
        con.execute("UPDATE payments SET disputed = 1, status = 'disputed' WHERE provider = ? AND payment_id = ?", (provider, obj["payment_id"]))
    else:
        con.commit()
        return "ignored"
    con.commit()
    return "applied"
