"""Webhook signature checks.

Stripe style: header `t=<unix>,v1=<hmac_sha256(secret, "<t>.<payload>")>` with a replay window.
PayPal style (simplified for the lab): header `X-Lab-Signature` = hmac_sha256(secret, payload).
"""
import hashlib
import hmac
import time


class SignatureError(Exception):
    pass


def sign_stripe(secret: str, payload: bytes, timestamp: int | None = None) -> str:
    ts = int(timestamp if timestamp is not None else time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify_stripe(secret: str, payload: bytes, header: str, tolerance: int = 300, now: float | None = None) -> None:
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts, sig = int(parts["t"]), parts["v1"]
    except (KeyError, ValueError):
        raise SignatureError("malformed signature header")
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise SignatureError("signature mismatch")
    if abs((now if now is not None else time.time()) - ts) > tolerance:
        raise SignatureError("timestamp outside the tolerance window (possible replay)")


def sign_paypal(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def verify_paypal(secret: str, payload: bytes, header: str) -> None:
    if not hmac.compare_digest(sign_paypal(secret, payload), header or ""):
        raise SignatureError("signature mismatch")
