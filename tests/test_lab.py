import json
import time

import pytest
from fastapi.testclient import TestClient

from app import db, handlers
from app.main import create_app
from app.reconcile import reconcile
from app.signing import SignatureError, sign_paypal, sign_stripe, verify_stripe


def stripe_post(client, event, secret="whsec_test", ts=None):
    body = json.dumps(event).encode()
    return client.post("/webhooks/stripe", content=body, headers={"Stripe-Signature": sign_stripe(secret, body, ts), "Content-Type": "application/json"})


@pytest.fixture()
def client():
    return TestClient(create_app(":memory:"))


PAID = {"id": "evt_1", "type": "payment.succeeded", "data": {"payment_id": "pi_1", "order_id": "A-1", "amount": 4900, "currency": "eur"}}


def test_valid_signature_is_accepted(client):
    r = stripe_post(client, PAID)
    assert r.status_code == 200 and r.json() == {"result": "applied"}


def test_wrong_secret_is_rejected(client):
    assert stripe_post(client, PAID, secret="wrong").status_code == 400


def test_old_timestamp_is_rejected_as_replay(client):
    assert stripe_post(client, PAID, ts=int(time.time()) - 3600).status_code == 400


def test_malformed_header_raises():
    with pytest.raises(SignatureError):
        verify_stripe("s", b"{}", "garbage")


def test_retry_of_the_same_event_is_ignored(client):
    stripe_post(client, PAID)
    assert stripe_post(client, PAID).json() == {"result": "duplicate"}
    n = client.app.state.con.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    assert n == 1


def test_partial_then_full_refund():
    con = db.connect()
    handlers.apply_event(con, "stripe", PAID)
    handlers.apply_event(con, "stripe", {"id": "evt_2", "type": "payment.refunded", "data": {"payment_id": "pi_1", "amount": 1000}})
    assert con.execute("SELECT status FROM payments").fetchone()[0] == "partially_refunded"
    handlers.apply_event(con, "stripe", {"id": "evt_3", "type": "payment.refunded", "data": {"payment_id": "pi_1", "amount": 3900}})
    assert con.execute("SELECT status, refunded FROM payments").fetchone()[:] == ("refunded", 4900)


def test_dispute_marks_payment():
    con = db.connect()
    handlers.apply_event(con, "paypal", {"id": "WH-1", "type": "PAYMENT.CAPTURE.COMPLETED", "data": {"payment_id": "PP-1", "amount": 2500, "currency": "usd"}})
    handlers.apply_event(con, "paypal", {"id": "WH-2", "type": "CUSTOMER.DISPUTE.CREATED", "data": {"payment_id": "PP-1"}})
    assert con.execute("SELECT status, disputed FROM payments").fetchone()[:] == ("disputed", 1)


def test_paypal_endpoint_checks_its_own_signature(client):
    body = json.dumps({"id": "WH-1", "type": "PAYMENT.CAPTURE.COMPLETED", "data": {"payment_id": "PP-1", "amount": 100, "currency": "usd"}}).encode()
    ok = client.post("/webhooks/paypal", content=body, headers={"X-Lab-Signature": sign_paypal("paypal_test", body)})
    bad = client.post("/webhooks/paypal", content=body, headers={"X-Lab-Signature": "nope"})
    assert ok.status_code == 200 and bad.status_code == 400


def test_reconciliation_finds_all_three_kinds_of_mismatch():
    con = db.connect()
    handlers.apply_event(con, "stripe", PAID)
    handlers.apply_event(con, "stripe", {"id": "e2", "type": "payment.succeeded", "data": {"payment_id": "pi_2", "amount": 500, "currency": "eur"}})
    export = [
        {"provider": "stripe", "payment_id": "pi_1", "amount": "4900", "currency": "EUR", "status": "paid"},      # matches
        {"provider": "stripe", "payment_id": "pi_2", "amount": "600", "currency": "EUR", "status": "paid"},       # amount differs
        {"provider": "stripe", "payment_id": "pi_3", "amount": "100", "currency": "EUR", "status": "paid"},       # no webhook
    ]
    problems = [i["problem"] for i in reconcile(con, export)]
    assert any("amount differs" in p for p in problems)
    assert any("no webhook received" in p for p in problems)
    assert len(problems) == 2
