"""Verify Meta callbacks and apply confirmed statuses to controlled test jobs."""

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .job_worker import REAL_TEST_CHANNEL
from .models import MessageJob

MAX_BODY_BYTES = 1_000_000
_SIGNATURE = re.compile(r"sha256=([0-9a-fA-F]{64})\Z")
_FAILURE_CODE = re.compile(r"[0-9]{1,9}\Z")
_STATUS_RANK = {"sent": 1, "delivered": 2, "read": 3}


def challenge_is_valid(mode: str | None, token: str | None) -> bool:
    secret = os.getenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "")
    return bool(secret and mode == "subscribe" and token is not None and hmac.compare_digest(token, secret))


def signature_is_valid(body: bytes, header: str | None) -> bool:
    secret = os.getenv("WHATSAPP_APP_SECRET", "")
    match = _SIGNATURE.fullmatch(header or "")
    if not secret or not match:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(match.group(1).lower(), expected)


def status_events(body: bytes) -> list[tuple[str, str, datetime, str | None]]:
    """Ignore all unrelated or malformed events after signature verification."""
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return []
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return []
    expected_phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    if not expected_phone_id:
        return []
    events = []
    for entry in payload.get("entry", []) if isinstance(payload.get("entry"), list) else []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes", []) if isinstance(entry.get("changes"), list) else []:
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue
            value = change.get("value")
            if not isinstance(value, dict) or value.get("messaging_product") != "whatsapp":
                continue
            metadata = value.get("metadata")
            if not isinstance(metadata, dict) or metadata.get("phone_number_id") != expected_phone_id:
                continue
            for status in value.get("statuses", []) if isinstance(value.get("statuses"), list) else []:
                if not isinstance(status, dict):
                    continue
                message_id, name, raw_time = status.get("id"), status.get("status"), status.get("timestamp")
                if not isinstance(message_id, str) or not message_id or len(message_id) > 512 or name not in {*_STATUS_RANK, "failed"}:
                    continue
                try:
                    if isinstance(raw_time, bool) or not str(raw_time).isdigit():
                        continue
                    event_at = datetime.fromtimestamp(int(raw_time), timezone.utc)
                except (OverflowError, OSError, ValueError):
                    continue
                code = None
                if name == "failed" and isinstance(status.get("errors"), list):
                    for error in status["errors"]:
                        if isinstance(error, dict) and not isinstance(error.get("code"), bool):
                            candidate = str(error.get("code", ""))
                            if _FAILURE_CODE.fullmatch(candidate):
                                code = candidate
                                break
                events.append((message_id, name, event_at, code))
    return events


def apply_status_events(db: Session, events: list[tuple[str, str, datetime, str | None]]) -> int:
    """Serialize updates across callback workers and keep delivery evidence monotonic."""
    if not events:
        return 0
    db.execute(text("BEGIN IMMEDIATE"))
    changed = 0
    try:
        for message_id, name, event_at, failure_code in events:
            matches = list(db.scalars(select(MessageJob).where(
                MessageJob.provider_message_id == message_id,
                MessageJob.delivery_mode == "real_test",
                MessageJob.channel == REAL_TEST_CHANNEL,
                MessageJob.job_type == "thank_you",
            ).limit(2)))
            if len(matches) != 1:
                continue
            job = matches[0]
            if job.status not in {"accepted", "delivered"} or job.delivery_status == "failed":
                continue
            current_rank = _STATUS_RANK.get(job.delivery_status, 0)
            if name == "failed":
                if current_rank >= 2 or job.status == "delivered":
                    continue
                job.status = "failed"
                job.reason = "meta_delivery_failed"
                job.completed_at = event_at
                job.delivery_failure_code = failure_code
            else:
                if _STATUS_RANK[name] <= current_rank:
                    continue
                if name in {"delivered", "read"}:
                    job.status = "delivered"
                    if job.delivered_at is None:
                        job.delivered_at = event_at
                    job.completed_at = job.delivered_at
            job.state_version += 1
            job.delivery_status = name
            job.delivery_event_at = event_at
            # Flush each transition so the existing SQLite guard sees one
            # state_version increment per status change in a batched callback.
            db.flush()
            changed += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    return changed
