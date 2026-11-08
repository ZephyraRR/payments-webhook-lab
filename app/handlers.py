"""Business logic for payment events.

Rules that real webhook traffic forces on you:
* the same event can arrive twice (provider retries)            -> idempotent by (provider, event id)
* a refund can arrive before the payment it refers to           -> parked in `pending`, applied when the payment shows up
* the same payment event can be sent again under a new id       -> amount and order are updated, refunds already booked are kept
* a refund can be larger than the payment                       -> clamped and written to `anomalies`
* a malformed event must not be marked as processed             -> validated first, everything runs in one transaction
"""
from sqlite3 import Connection

PAID = ("payment.succeeded", "PAYMENT.CAPTURE.COMPLETED")
REFUNDED = ("payment.refunded", "PAYMENT.CAPTURE.REFUNDED")
DISPUTED = ("dispute.created", "CUSTOMER.DISPUTE.CREATED")


class InvalidEvent(ValueError):
    pass


def validate(event: dict) -> None:
    if not isinstance(event, dict) or not event.get("id") or not event.get("type") or not isinstance(event.get("data"), dict):
        raise InvalidEvent("event needs id, type and data")
    etype, data = event["type"], event["data"]
    if etype in PAID + REFUNDED + DISPUTED and not data.get("payment_id"):
        raise InvalidEvent("data.payment_id is required")
    if etype in PAID + REFUNDED:
        try:
            amount = int(data["amount"])
        except (KeyError, TypeError, ValueError):
            raise InvalidEvent("data.amount must be an integer in minor units")
        if amount <= 0:
            raise InvalidEvent("data.amount must be positive")
    if etype in PAID and not str(data.get("currency", "")).strip():
        raise InvalidEvent("data.currency is required")


def _note(con: Connection, provider: str, payment_id: str, text: str) -> None:
    con.execute("INSERT INTO anomalies(provider, payment_id, note) VALUES (?, ?, ?)", (provider, payment_id, text))


def _book_refund(con: Connection, provider: str, payment_id: str, amount: int) -> None:
    row = con.execute("SELECT amount, refunded FROM payments WHERE provider=? AND payment_id=?", (provider, payment_id)).fetchone()
    room = row["amount"] - row["refunded"]
    booked = min(amount, room)
    if booked < amount:
        _note(con, provider, payment_id, f"refund of {amount} exceeds the remaining {room}; booked {booked}")
    total = row["refunded"] + booked
    status = "refunded" if total >= row["amount"] else "partially_refunded"
    con.execute("UPDATE payments SET refunded=?, status=CASE WHEN disputed=1 THEN 'disputed' ELSE ? END WHERE provider=? AND payment_id=?",
                (total, status, provider, payment_id))


def _apply_pending(con: Connection, provider: str, payment_id: str) -> None:
    for p in con.execute("SELECT * FROM pending WHERE provider=? AND payment_id=? ORDER BY rowid", (provider, payment_id)).fetchall():
        if p["kind"] == "refund":
            _book_refund(con, provider, payment_id, p["amount"])
        else:
            con.execute("UPDATE payments SET disputed=1, status='disputed' WHERE provider=? AND payment_id=?", (provider, payment_id))
        con.execute("DELETE FROM pending WHERE provider=? AND event_id=?", (provider, p["event_id"]))


def apply_event(con: Connection, provider: str, event: dict) -> str:
    validate(event)
    etype, data, eid = event["type"], event["data"], str(event["id"])
    with con:                                   # one transaction: a crash never leaves a half-processed event
        if con.execute("INSERT OR IGNORE INTO events(provider, event_id, type) VALUES (?, ?, ?)", (provider, eid, etype)).rowcount == 0:
            return "duplicate"
        pid = str(data.get("payment_id", ""))
        known = con.execute("SELECT amount FROM payments WHERE provider=? AND payment_id=?", (provider, pid)).fetchone() if pid else None
        if etype in PAID:
            amount = int(data["amount"])
            if known is not None and known["amount"] != amount:
                _note(con, provider, pid, f"payment event repeated with a different amount: {known['amount']} then {amount}")
            con.execute(
                "INSERT INTO payments(provider, payment_id, order_id, amount, currency, status) VALUES (?, ?, ?, ?, ?, 'paid') "
                "ON CONFLICT(provider, payment_id) DO UPDATE SET order_id=COALESCE(excluded.order_id, order_id), amount=excluded.amount, currency=excluded.currency",
                (provider, pid, data.get("order_id"), amount, str(data["currency"]).upper()),
            )
            _apply_pending(con, provider, pid)
            return "applied"
        if etype in REFUNDED:
            if known is None:
                con.execute("INSERT INTO pending(provider, payment_id, event_id, kind, amount) VALUES (?, ?, ?, 'refund', ?)", (provider, pid, eid, int(data["amount"])))
                return "parked"
            _book_refund(con, provider, pid, int(data["amount"]))
            return "applied"
        if etype in DISPUTED:
            if known is None:
                con.execute("INSERT INTO pending(provider, payment_id, event_id, kind, amount) VALUES (?, ?, ?, 'dispute', 0)", (provider, pid, eid))
                return "parked"
            con.execute("UPDATE payments SET disputed=1, status='disputed' WHERE provider=? AND payment_id=?", (provider, pid))
            return "applied"
        return "ignored"
