import json
import os

from fastapi import FastAPI, Header, HTTPException, Request

from . import db, handlers
from .signing import SignatureError, verify_paypal, verify_stripe

STRIPE_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "whsec_test")
PAYPAL_SECRET = os.environ.get("PAYPAL_WEBHOOK_SECRET", "paypal_test")


def create_app(db_path: str = os.environ.get("LAB_DB", "lab.sqlite3")) -> FastAPI:
    app = FastAPI(title="payments-webhook-lab")
    con = db.connect(db_path)

    def process(provider: str, body: bytes) -> dict:
        try:
            event = json.loads(body)
        except ValueError:
            raise HTTPException(status_code=400, detail="body is not valid JSON")
        try:
            return {"result": handlers.apply_event(con, provider, event)}
        except handlers.InvalidEvent as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/webhooks/stripe")
    async def stripe(request: Request, stripe_signature: str = Header(default="")):
        body = await request.body()
        try:
            verify_stripe(STRIPE_SECRET, body, stripe_signature)
        except SignatureError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return process("stripe", body)

    @app.post("/webhooks/paypal")
    async def paypal(request: Request, x_lab_signature: str = Header(default="")):
        body = await request.body()
        try:
            verify_paypal(PAYPAL_SECRET, body, x_lab_signature)
        except SignatureError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return process("paypal", body)

    @app.get("/health")
    def health():
        return {"ok": True}

    app.state.con = con
    return app


app = create_app()
