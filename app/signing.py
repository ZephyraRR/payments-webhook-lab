"""Webhook signature checks.

Stripe style: header `t=<unix>,v1=<hmac_sha256(secret, "<t>.<payload>")>` with a replay window. The header may carry several `v1` values
(Stripe does that while a signing secret is being rotated) and extra schemes such as `v0`; any matching `v1` is accepted.
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


def _parse(header: str) -> tuple[int, list[str]]:
    ts = None
    sigs: list[str] = []
    for part in (header or "").split(","):
        key, sep, value = part.strip().partition("=")
        if not sep:
            continue
        if key == "t":
            try:
                ts = int(value)
            except ValueError:
                raise SignatureError("malformed signature header")
        elif key == "v1":
            sigs.append(value)
    if ts is None or not sigs:
        raise SignatureError("malformed signature header")
    return ts, sigs


def verify_stripe(secret: str, payload: bytes, header: str, tolerance: int = 300, now: float | None = None) -> None:
    ts, sigs = _parse(header)
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in sigs):
        raise SignatureError("signature mismatch")
    if abs((now if now is not None else time.time()) - ts) > tolerance:
        raise SignatureError("timestamp outside the tolerance window (possible replay)")


def sign_paypal(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def verify_paypal(secret: str, payload: bytes, header: str) -> None:
    if not hmac.compare_digest(sign_paypal(secret, payload), header or ""):
        raise SignatureError("signature mismatch")
