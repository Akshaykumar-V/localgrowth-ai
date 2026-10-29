"""Durable simulation and at-most-once controlled test dispatch for SQLite."""

import hmac
import os
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Campaign, Customer, MessageJob, Purchase, WhatsAppDeliveryAttempt, utcnow
from .security import phone_lookup_hash
from .services import as_utc, has_permission
from .whatsapp import SAFE_SEND_ERROR_CODES, WhatsAppConfigurationError, WhatsAppSendError, WhatsAppTestConfig, send_test_template


JOB_STATES = ("queued", "processing", "accepted", "simulated", "delivered", "failed", "skipped")
REAL_TEST_CHANNEL = "whatsapp_real_test"
TEST_TEMPLATE_LABEL = "Controlled automation test of a Meta-approved template; not a production thank-you message."


def real_test_enabled() -> bool:
    return (
        os.getenv("WHATSAPP_AUTOMATION_MODE", "simulation").strip().lower() == "real_test"
        and os.getenv("WHATSAPP_AUTOMATION_ENABLED", "false").strip().lower() == "true"
    )


def delivery_mode_for_purchase(idempotency_key: str | None) -> str:
    test_key = os.getenv("WHATSAPP_AUTOMATION_PURCHASE_KEY", "").strip()
    if real_test_enabled() and test_key and idempotency_key == test_key:
        return "real_test"
    return "simulation"


def validate_real_test_purchase(idempotency_key: str | None, phone: str, thank_you: bool | None) -> str | None:
    """Fail a requested test before purchase creation instead of silently simulating."""
    if not real_test_enabled():
        return "real_test_disabled"
    test_key = os.getenv("WHATSAPP_AUTOMATION_PURCHASE_KEY", "").strip()
    if not test_key or idempotency_key != test_key:
        return "test_purchase_not_selected"
    if thank_you is not True:
        return "thank_you_permission_missing"
    try:
        config = WhatsAppTestConfig.from_env()
        if not hmac.compare_digest(phone_lookup_hash(phone), phone_lookup_hash(config.recipient)):
            return "recipient_not_allowlisted"
    except (WhatsAppConfigurationError, RuntimeError):
        return "invalid_real_test_configuration"
    return None


def _eligible_config(db: Session, job: MessageJob) -> tuple[WhatsAppTestConfig | None, str | None]:
    if not real_test_enabled():
        return None, "real_test_disabled"
    if job.job_type != "thank_you" or job.channel != REAL_TEST_CHANNEL or job.campaign_id:
        return None, "unsupported_real_test_job"
    purchase = db.get(Purchase, job.purchase_id) if job.purchase_id else None
    test_key = os.getenv("WHATSAPP_AUTOMATION_PURCHASE_KEY", "").strip()
    if not purchase or purchase.customer_id != job.customer_id or not test_key or purchase.idempotency_key != test_key:
        return None, "test_purchase_not_selected"
    try:
        config = WhatsAppTestConfig.from_env()
        customer = db.get(Customer, job.customer_id)
        # Compare full-number lookup hashes, never suffixes; never decrypt customer phones.
        if not customer or not hmac.compare_digest(customer.phone_lookup_hash, phone_lookup_hash(config.recipient)):
            return None, "recipient_not_allowlisted"
    except (WhatsAppConfigurationError, RuntimeError):
        return None, "invalid_real_test_configuration"
    return config, None


def _finish_campaign(db: Session, job: MessageJob, now: datetime) -> None:
    if not job.campaign_id:
        return
    db.flush()
    remaining = db.scalar(select(func.count()).select_from(MessageJob).where(
        MessageJob.campaign_id == job.campaign_id,
        MessageJob.status.in_(("queued", "processing")),
    ))
    campaign = db.get(Campaign, job.campaign_id)
    if campaign and not remaining:
        campaign.status = "simulated"
        campaign.completed_at = now


def _claim(factory, job_id: str, now: datetime):
    with factory() as db:
        # Serialize the eligibility check + state transition across SQLite processes.
        db.execute(text("BEGIN IMMEDIATE"))
        job = db.get(MessageJob, job_id)
        if not job or job.status != "queued":
            return False, None
        if job.job_type == "thank_you" and (job.scheduled_at is None or as_utc(job.scheduled_at) > as_utc(now)):
            return False, None

        reason = None
        config = None
        if not has_permission(db, job.customer_id, job.job_type):
            reason = f"{job.job_type}_permission_missing"
        elif job.delivery_mode == "real_test":
            config, reason = _eligible_config(db, job)
        elif job.delivery_mode != "simulation" or job.channel != "whatsapp_simulated":
            reason = "unsupported_delivery_mode"

        if reason:
            if job.delivery_mode == "real_test":
                job.state_version += 1
            job.status, job.reason, job.completed_at = "skipped", reason, now
        elif job.delivery_mode == "simulation":
            job.status, job.reason, job.completed_at = "simulated", None, now
        else:
            dedupe_key = f"thank_you:{job.purchase_id}"
            previous = db.scalar(select(WhatsAppDeliveryAttempt).where(WhatsAppDeliveryAttempt.dedupe_key == dedupe_key))
            if previous:
                job.state_version += 1
                job.status, job.reason, job.completed_at = "skipped", "duplicate_dispatch", now
            else:
                job.state_version += 1
                job.status = "processing"
                job.processing_started_at = now
                job.reason = "provider_outcome_unconfirmed_no_retry"
                db.add(WhatsAppDeliveryAttempt(
                    dedupe_key=dedupe_key, job_id=job.id, status="processing",
                    reason=job.reason, template_name=config.template_name,
                    language_code=config.language_code, created_at=now,
                ))
                # Commit BEFORE HTTP. A crash after this point never automatically retries.
                db.commit()
                return True, config
        _finish_campaign(db, job, now)
        db.commit()
        return True, None


def _recheck(factory, job_id: str) -> tuple[WhatsAppTestConfig | None, str | None]:
    """Fresh consent/config check at the send boundary, after the claim is durable."""
    with factory() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        job = db.get(MessageJob, job_id)
        attempt = db.scalar(select(WhatsAppDeliveryAttempt).where(WhatsAppDeliveryAttempt.job_id == job_id))
        if not job or job.status != "processing" or not attempt or attempt.status != "processing":
            db.rollback()
            return None, "claim_lost"
        reason = None
        config = None
        if not has_permission(db, job.customer_id, "thank_you"):
            reason = "thank_you_permission_missing"
        else:
            config, reason = _eligible_config(db, job)
        if reason:
            job.state_version += 1
            job.status, job.reason, job.completed_at = "skipped", reason, utcnow()
            attempt.status, attempt.reason, attempt.finished_at = "skipped", reason, utcnow()
        db.commit()
        return config, reason


def process_queued_jobs(db: Session, now: datetime | None = None, *, sender=None) -> int:
    """Use independent short transactions. Never retry a processing/terminal attempt."""
    now = as_utc(now or utcnow())
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False, autoflush=False)
    with factory() as reader:
        ids = list(reader.scalars(select(MessageJob.id).where(
            MessageJob.status == "queued",
            ((MessageJob.job_type == "thank_you") & (MessageJob.scheduled_at <= now))
            | ((MessageJob.job_type == "promotional") & MessageJob.scheduled_at.is_(None)),
        ).order_by(MessageJob.created_at, MessageJob.id)))

    handled = 0
    for job_id in ids:
        claimed, config = _claim(factory, job_id, now)
        handled += int(claimed)
        if config is None:
            continue
        config, recheck_reason = _recheck(factory, job_id)
        if recheck_reason or config is None:
            continue
        provider_id, reason = None, None
        try:
            provider_id = (sender or send_test_template)(config)
            if not isinstance(provider_id, str) or not provider_id:
                reason = "provider_outcome_unknown_no_retry"
        except WhatsAppSendError as exc:
            reason = exc.reason_code if exc.reason_code in SAFE_SEND_ERROR_CODES else "provider_outcome_unknown_no_retry"
        except Exception:
            # Unknown failures may occur after Meta accepted; never log exception payloads.
            reason = "provider_outcome_unknown_no_retry"
        with factory() as writer:
            job = writer.get(MessageJob, job_id)
            attempt = writer.scalar(select(WhatsAppDeliveryAttempt).where(WhatsAppDeliveryAttempt.job_id == job_id))
            if not job or job.status != "processing" or not attempt or attempt.status != "processing":
                continue
            job.state_version += 1
            if reason:
                job.status, job.reason, job.completed_at = "failed", reason, utcnow()
                attempt.status, attempt.reason = "failed", reason
            else:
                job.status, job.reason = "accepted", None
                job.provider_message_id = provider_id
                job.accepted_at = utcnow()
                attempt.status, attempt.reason, attempt.provider_message_id = "accepted", None, provider_id
                # delivered_at remains NULL. Only a verified provider delivery event can set it.
            attempt.finished_at = utcnow()
            writer.commit()
    return handled
