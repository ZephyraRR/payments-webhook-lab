"""Sends signed demo events to a running server: python simulate.py http://127.0.0.1:8000"""
import json
import sys

import httpx

from app.signing import sign_paypal, sign_stripe

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
EVENTS = [
    ("stripe", {"id": "evt_1", "type": "payment.succeeded", "data": {"payment_id": "pi_100", "order_id": "A-1", "amount": 4900, "currency": "eur"}}),
    ("stripe", {"id": "evt_2", "type": "payment.refunded", "data": {"payment_id": "pi_100", "amount": 1000}}),
    ("stripe", {"id": "evt_2", "type": "payment.refunded", "data": {"payment_id": "pi_100", "amount": 1000}}),  # a provider retry: must be ignored
    ("paypal", {"id": "WH-9", "type": "PAYMENT.CAPTURE.COMPLETED", "data": {"payment_id": "PP-7", "order_id": "A-2", "amount": 2500, "currency": "usd"}}),
    ("paypal", {"id": "WH-10", "type": "CUSTOMER.DISPUTE.CREATED", "data": {"payment_id": "PP-7"}}),
]

for provider, ev in EVENTS:
    body = json.dumps(ev).encode()
    headers = {"Content-Type": "application/json"}
    if provider == "stripe":
        headers["Stripe-Signature"] = sign_stripe("whsec_test", body)
    else:
        headers["X-Lab-Signature"] = sign_paypal("paypal_test", body)
    r = httpx.post(f"{BASE}/webhooks/{provider}", content=body, headers=headers)
    print(provider, ev["id"], r.status_code, r.json())
