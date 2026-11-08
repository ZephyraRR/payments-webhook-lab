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


# --- regression tests for bugs found in review ---------------------------------------------------------------------------

def post_raw(client, body: bytes, secret="whsec_test"):
    return client.post("/webhooks/stripe", content=body, headers={"Stripe-Signature": sign_stripe(secret, body), "Content-Type": "application/json"})


def test_refund_before_payment_is_parked_and_applied_when_the_payment_arrives():
    con = db.connect()
    assert handlers.apply_event(con, "stripe", {"id": "r1", "type": "payment.refunded", "data": {"payment_id": "pi_9", "amount": 1000}}) == "parked"
    assert con.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0
    handlers.apply_event(con, "stripe", {"id": "p1", "type": "payment.succeeded", "data": {"payment_id": "pi_9", "amount": 5000, "currency": "eur"}})
    row = con.execute("SELECT status, refunded FROM payments").fetchone()
    assert (row["status"], row["refunded"]) == ("partially_refunded", 1000)
    assert con.execute("SELECT COUNT(*) FROM pending").fetchone()[0] == 0


def test_dispute_before_payment_is_parked_too():
    con = db.connect()
    assert handlers.apply_event(con, "paypal", {"id": "d1", "type": "CUSTOMER.DISPUTE.CREATED", "data": {"payment_id": "PP-5"}}) == "parked"
    handlers.apply_event(con, "paypal", {"id": "c1", "type": "PAYMENT.CAPTURE.COMPLETED", "data": {"payment_id": "PP-5", "amount": 700, "currency": "usd"}})
    assert con.execute("SELECT status FROM payments").fetchone()[0] == "disputed"


def test_repeated_payment_event_under_a_new_id_keeps_the_refunds_already_booked():
    con = db.connect()
    handlers.apply_event(con, "stripe", PAID)
    handlers.apply_event(con, "stripe", {"id": "r", "type": "payment.refunded", "data": {"payment_id": "pi_1", "amount": 900}})
    handlers.apply_event(con, "stripe", {**PAID, "id": "evt_again"})
    row = con.execute("SELECT status, refunded FROM payments").fetchone()
    assert (row["status"], row["refunded"]) == ("partially_refunded", 900)


def test_refund_larger_than_the_payment_is_clamped_and_flagged():
    con = db.connect()
    handlers.apply_event(con, "stripe", PAID)
    handlers.apply_event(con, "stripe", {"id": "big", "type": "payment.refunded", "data": {"payment_id": "pi_1", "amount": 999999}})
    row = con.execute("SELECT status, refunded FROM payments").fetchone()
    assert (row["status"], row["refunded"]) == ("refunded", 4900)
    assert "exceeds" in con.execute("SELECT note FROM anomalies").fetchone()[0]


def test_a_dispute_is_not_hidden_by_a_later_refund():
    con = db.connect()
    handlers.apply_event(con, "stripe", PAID)
    handlers.apply_event(con, "stripe", {"id": "d", "type": "dispute.created", "data": {"payment_id": "pi_1"}})
    handlers.apply_event(con, "stripe", {"id": "r", "type": "payment.refunded", "data": {"payment_id": "pi_1", "amount": 100}})
    assert con.execute("SELECT status FROM payments").fetchone()[0] == "disputed"


def test_invalid_json_with_a_valid_signature_is_a_400_not_a_500(client):
    assert post_raw(client, b"not json").status_code == 400


def test_missing_fields_are_a_400_and_the_event_is_not_marked_processed(client):
    bad = {"id": "evt_bad", "type": "payment.succeeded", "data": {"payment_id": "pi_1"}}      # no amount, no currency
    assert post_raw(client, json.dumps(bad).encode()).status_code == 400
    assert client.app.state.con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    good = {**bad, "data": {"payment_id": "pi_1", "amount": 100, "currency": "eur"}}
    assert post_raw(client, json.dumps(good).encode()).json() == {"result": "applied"}     # the same id works once it is valid


@pytest.mark.parametrize("amount", [0, -5, "abc", None])
def test_bad_amounts_are_rejected(amount):
    con = db.connect()
    with pytest.raises(handlers.InvalidEvent):
        handlers.apply_event(con, "stripe", {"id": "x", "type": "payment.succeeded", "data": {"payment_id": "p", "amount": amount, "currency": "eur"}})


def test_stripe_header_with_two_v1_values_during_secret_rotation():
    body = b"{}"
    ts = int(time.time())   # both signatures and the header share one timestamp
    header = f"t={ts},v1=deadbeef,{sign_stripe('new', body, ts).split(',')[1]},v0=ignored"
    verify_stripe("new", body, header)


def test_orphans_and_anomalies_show_up_in_the_reconciliation():
    con = db.connect()
    handlers.apply_event(con, "stripe", {"id": "r1", "type": "payment.refunded", "data": {"payment_id": "pi_lost", "amount": 100}})
    problems = [i["problem"] for i in reconcile(con, [])]
    assert any("never did" in p for p in problems)
