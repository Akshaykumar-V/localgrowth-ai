"""Shared WhatsApp webhook routes for the API and isolated public listener."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .db import SessionLocal
from .whatsapp_webhook import MAX_BODY_BYTES, apply_status_events, challenge_is_valid, signature_is_valid, status_events

router = APIRouter()


@router.get("/api/webhooks/whatsapp")
def verify_whatsapp_webhook(request: Request):
    params = request.query_params
    if not challenge_is_valid(params.get("hub.mode"), params.get("hub.verify_token")):
        raise HTTPException(status_code=403, detail="Webhook verification failed.")
    challenge = params.get("hub.challenge")
    if challenge is None:
        raise HTTPException(status_code=403, detail="Webhook verification failed.")
    return PlainTextResponse(challenge)


@router.post("/api/webhooks/whatsapp")
async def receive_whatsapp_webhook(request: Request):
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Webhook payload too large.")
        chunks.append(chunk)
    raw_body = b"".join(chunks)
    if not signature_is_valid(raw_body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="Webhook signature invalid.")
    events = status_events(raw_body)
    if events:
        with SessionLocal() as db:
            apply_status_events(db, events)
    return {"data": {"received": True}}
