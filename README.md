# payments-webhook-lab

A small, runnable reference for the part of e-commerce support that goes wrong most often: **payment webhooks**.

Providers retry events, send them out of order, and sometimes a payment never reaches your database at all. The result is a customer who paid but has no order, or a refund that support cannot find. This lab shows a safe way to receive the events and a report that finds the mismatches.

## What it does

- **Signature verification.** Stripe-style header (`t=...,v1=...`, HMAC-SHA256 with a replay window) and a simplified PayPal-style header.
- **Idempotency.** Every event id is stored once per provider, so a retry is answered with `duplicate` and changes nothing.
- **Payment states.** `paid`, `partially_refunded`, `refunded` and `disputed`, updated from refund and dispute events.
- **Reconciliation.** Compare the recorded payments with a provider export (CSV) and list every mismatch: missing webhook, different amount, different status, recorded but not in the export.

It runs against local demo events only. No real keys, no network calls to payment providers.

## Run it

```bash
pip install -r requirements.txt
uvicorn app.main:app            # terminal 1
python simulate.py              # terminal 2: sends signed demo events, one is a retry
python -m app.reconcile lab.sqlite3 export.csv
```

`export.csv` columns: `provider,payment_id,amount,currency,status` (amounts in minor units).

## Tests

```bash
python -m pytest -q
```

Nine tests cover valid and invalid signatures, replay protection, malformed headers, duplicate events, partial then full refunds, disputes, the PayPal endpoint and the three kinds of reconciliation mismatch.

## Layout

```
app/signing.py     signature creation and verification
app/db.py          SQLite schema (events, payments)
app/handlers.py    idempotent event handling and state changes
app/main.py        FastAPI endpoints /webhooks/stripe and /webhooks/paypal
app/reconcile.py   reconciliation report (also a CLI)
simulate.py        demo traffic
```

## Why this exists

I handled technical and billing tickets for a global digital-goods platform, verified Stripe, PayPal and crypto payments and read webhook payloads to find why a delivery failed. This repository is the engineering side of that work, written so anyone can run and review it.

## Limits (honest)

The PayPal signature here is simplified, real PayPal verification calls their API. Amounts are integers in minor units and the schema is intentionally small. It is a reference and a learning tool, not a payments product.
