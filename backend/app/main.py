import base64
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config  # noqa: F401
from .db import Base, SessionLocal, engine, get_db
from .models import Campaign, Customer, MessageJob, Permission, Purchase, PurchaseItem, utcnow
from .migrate import migrate_database
from .job_worker import (JOB_STATES, REAL_TEST_CHANNEL, TEST_TEMPLATE_LABEL, delivery_mode_for_purchase,
                         real_test_enabled, validate_real_test_purchase)
from .schemas import CampaignPreviewRequest, CreatePurchaseRequest, SimulationRequest
from .security import encrypt_phone, phone_lookup_hash, validate_secrets
from .services import (
    CHANNEL,
    amount,
    as_utc,
    build_campaign_message,
    build_thank_you_message,
    eligible_campaign_customers,
    has_permission,
    iso,
    make_purchase_response,
    message_job_public,
    permission_map,
    phone_mask_for,
    process_queued_jobs,
    purchase_fingerprint,
)
from .webhook_routes import router as whatsapp_webhook_router


# The customer search endpoint accepts phone digits in its query string. Disable
# Uvicorn's default access logger so request URLs cannot leak those digits.
logging.getLogger("uvicorn.access").disabled = True
logger = logging.getLogger("uvicorn.error")


_worker_stop = threading.Event()
_worker_thread: threading.Thread | None = None


def _worker_loop() -> None:
    interval = max(0.1, float(os.getenv("JOB_WORKER_INTERVAL_SECONDS", "1")))
    while not _worker_stop.is_set():
        try:
            with SessionLocal() as db:
                process_queued_jobs(db)
        except Exception as exc:
            # Do not log exception text: provider or database errors can contain secrets.
            logger.error("Background worker iteration failed (%s); no automatic send retry.", type(exc).__name__)
        _worker_stop.wait(interval)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _worker_thread
    validate_secrets()
    migrate_database(engine)
    _worker_stop.clear()
    worker_enabled = os.getenv("JOB_WORKER_ENABLED", "true").lower() not in {"0", "false", "no"}
    configured_mode = os.getenv("WHATSAPP_AUTOMATION_MODE", "simulation").strip().lower()
    safe_mode = configured_mode if configured_mode in {"simulation", "real_test"} else "invalid"
    logger.info("Job worker startup: pid=%s worker_enabled=%s automation_mode=%s real_test_enabled=%s test_key_configured=%s",
                os.getpid(), worker_enabled, safe_mode, real_test_enabled(),
                bool(os.getenv("WHATSAPP_AUTOMATION_PURCHASE_KEY", "").strip()))
    if worker_enabled:
        _worker_thread = threading.Thread(target=_worker_loop, name="localgrowth-job-worker", daemon=True)
        _worker_thread.start()
    try:
        yield
    finally:
        _worker_stop.set()
        if _worker_thread:
            _worker_thread.join(timeout=2)
            _worker_thread = None


app = FastAPI(title="LocalGrowth AI Backend", version="0.1.0", lifespan=lifespan)
app.include_router(whatsapp_webhook_router)


def _request_id() -> str:
    return f"req_{uuid4()}"


def _error(code: str, message: str, status: int, details: list[dict] | None = None) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "request_id": _request_id()}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=body)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    details = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        details.append({"field": loc or "request", "issue": error.get("msg", "Invalid value.")})
    return _error("VALIDATION_ERROR", "Request validation failed.", 422, details)


@app.exception_handler(HTTPException)
async def http_error_handler(_request: Request, exc: HTTPException):
    mapping = {
        400: "INVALID_REQUEST",
        401: "UNAUTHENTICATED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        413: "PAYLOAD_TOO_LARGE",
        422: "VALIDATION_ERROR",
        500: "INTERNAL_ERROR",
        503: "SERVICE_UNAVAILABLE",
    }
    message = exc.detail if isinstance(exc.detail, str) else "Request could not be completed."
    return _error(mapping.get(exc.status_code, "INTERNAL_ERROR"), message, exc.status_code)


@app.exception_handler(Exception)
async def internal_error_handler(_request: Request, _exc: Exception):
    return _error("INTERNAL_ERROR", "Unexpected server error.", 500)


def _cursor_offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = int(base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii"))
        if value < 0:
            raise ValueError
        return value
    except (ValueError, UnicodeEncodeError, UnicodeDecodeError):
        raise HTTPException(status_code=422, detail="Invalid pagination cursor.") from None


def _next_cursor(offset: int, returned: int, limit: int) -> str | None:
    if returned < limit:
        return None
    return base64.urlsafe_b64encode(str(offset + returned).encode("ascii")).decode("ascii").rstrip("=")


def _customer_permissions_for_new(db: Session, customer_id: str, values: dict[str, bool | None], now: datetime) -> None:
    for kind in ("thank_you", "promotional"):
        value = values.get(kind)
        record = db.scalar(
            select(Permission).where(Permission.customer_id == customer_id, Permission.permission_type == kind)
        )
        if record is None:
            record = Permission(
                customer_id=customer_id,
                permission_type=kind,
                allowed=False,
                choice_recorded=False,
                recorded_at=None,
            )
            db.add(record)
        if value is not None:
            record.allowed = value
            record.choice_recorded = True
            record.recorded_at = now


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Service or database is temporarily unavailable.") from None
    return {"data": {"status": "ok", "database": "ok", "checked_at": iso(utcnow())}}


@app.post("/api/purchases", status_code=201)
def create_purchase(body: CreatePurchaseRequest, db: Session = Depends(get_db)):
    phone = body.customer.phone
    # Preserve fingerprints for requests created before the explicit test guard existed.
    fingerprint_data = body.model_dump(mode="json", exclude={"idempotency_key", "delivery_mode"})
    if body.delivery_mode is not None:
        fingerprint_data["delivery_mode"] = body.delivery_mode
    fingerprint = purchase_fingerprint(fingerprint_data)
    if body.idempotency_key:
        existing = db.scalar(select(Purchase).where(Purchase.idempotency_key == body.idempotency_key))
        if existing:
            if existing.request_fingerprint != fingerprint:
                raise HTTPException(status_code=409, detail="Idempotency key was already used with a different request.")
            return {"data": make_purchase_response(db, existing)}

    if body.delivery_mode == "real_test":
        reason = validate_real_test_purchase(body.idempotency_key, phone, body.permissions.thank_you)
        if reason:
            raise HTTPException(status_code=409, detail=f"Real-test request rejected: {reason}.")

    now = utcnow()
    try:
        customer = db.scalar(select(Customer).where(Customer.phone_lookup_hash == phone_lookup_hash(phone)))
        if customer is None:
            customer = Customer(
                name=body.customer.name,
                phone_ciphertext=encrypt_phone(phone),
                phone_lookup_hash=phone_lookup_hash(phone),
                phone_last4=phone[-4:],
                created_at=now,
            )
            db.add(customer)
            db.flush()
        else:
            customer.name = body.customer.name

        _customer_permissions_for_new(db, customer.id, body.permissions.model_dump(), now)
        db.flush()
        purchased_at = as_utc(body.purchase.purchased_at) if body.purchase.purchased_at else now
        purchase = Purchase(
            customer_id=customer.id,
            idempotency_key=body.idempotency_key,
            request_fingerprint=fingerprint if body.idempotency_key else None,
            total_amount=body.purchase.total_amount,
            currency=body.purchase.currency,
            purchased_at=purchased_at,
        )
        db.add(purchase)
        db.flush()
        item_data = body.purchase.items[0]
        item = PurchaseItem(
            purchase_id=purchase.id,
            name=item_data.name,
            category=item_data.category,
            quantity=item_data.quantity,
            unit_price=item_data.unit_price,
        )
        db.add(item)
        thank_you_job = None
        if has_permission(db, customer.id, "thank_you"):
            delay = max(0, int(os.getenv("THANK_YOU_DELAY_SECONDS", "420")))
            scheduled_at = now + timedelta(seconds=delay)
            delivery_mode = body.delivery_mode or delivery_mode_for_purchase(body.idempotency_key)
            thank_you_job = MessageJob(
                job_type="thank_you",
                channel=REAL_TEST_CHANNEL if delivery_mode == "real_test" else CHANNEL,
                delivery_mode=delivery_mode,
                status="queued",
                customer_id=customer.id,
                purchase_id=purchase.id,
                message_body=TEST_TEMPLATE_LABEL if delivery_mode == "real_test" else build_thank_you_message(customer, item),
                scheduled_at=scheduled_at,
                created_at=now,
            )
            db.add(thank_you_job)
        db.commit()
        db.refresh(purchase)
        return {"data": make_purchase_response(db, purchase)}
    except IntegrityError:
        db.rollback()
        if body.idempotency_key:
            existing = db.scalar(select(Purchase).where(Purchase.idempotency_key == body.idempotency_key))
            if existing and existing.request_fingerprint == fingerprint:
                return {"data": make_purchase_response(db, existing)}
            if existing:
                raise HTTPException(status_code=409, detail="Idempotency key was already used with a different request.") from None
        raise HTTPException(status_code=500, detail="Purchase could not be saved.") from None


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    customers_total = db.scalar(select(func.count()).select_from(Customer)) or 0
    purchases_total = db.scalar(select(func.count()).select_from(Purchase)) or 0
    amount_count = db.scalar(select(func.count()).select_from(Purchase).where(
        Purchase.total_amount.is_not(None), Purchase.currency == "INR"
    )) or 0
    revenue = db.scalar(select(func.coalesce(func.sum(Purchase.total_amount), 0)).where(Purchase.currency == "INR")) or 0
    campaigns = {
        status: db.scalar(select(func.count()).select_from(Campaign).where(Campaign.status == status)) or 0
        for status in ("draft", "approved", "simulating", "simulated", "failed")
    }
    jobs = {
        status: db.scalar(select(func.count()).select_from(MessageJob).where(MessageJob.status == status)) or 0
        for status in JOB_STATES
    }
    rows = db.execute(
        select(Purchase, Customer)
        .join(Customer, Purchase.customer_id == Customer.id)
        .order_by(Purchase.purchased_at.desc(), Purchase.id.desc())
        .limit(10)
    ).all()
    recent = []
    for purchase, customer in rows:
        item = db.scalar(select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id))
        recent.append({
            "purchase_id": purchase.id,
            "customer_id": customer.id,
            "customer_name": customer.name,
            "phone_masked": phone_mask_for(customer),
            "category": item.category if item else None,
            "total_amount": amount(purchase.total_amount),
            "currency": purchase.currency,
            "purchased_at": iso(purchase.purchased_at),
        })
    return {"data": {
        "customers_total": customers_total,
        "purchases_total": purchases_total,
        "purchases_with_amount": amount_count,
        "revenue": {"amount": float(revenue), "currency": "INR"},
        "campaigns": campaigns,
        "message_jobs": jobs,
        "recent_purchases": recent,
        "generated_at": iso(utcnow()),
    }}

@app.get("/api/customers")
def customers(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
    search: str | None = Query(default=None, max_length=120),
    db: Session = Depends(get_db),
):
    offset = _cursor_offset(cursor)
    query = select(Customer)
    if search:
        term = search.strip()
        digits = "".join(ch for ch in term if ch.isdigit())
        predicates = [Customer.name.ilike(f"%{term}%")]
        if len(digits) == 10:
            try:
                from .security import normalize_phone
                predicates.append(Customer.phone_lookup_hash == phone_lookup_hash(normalize_phone(digits)))
            except ValueError:
                pass
        elif digits:
            predicates.append(Customer.phone_last4.contains(digits[-4:]))
        query = query.where(or_(*predicates))
    query = query.order_by(Customer.created_at.desc(), Customer.id.desc()).offset(offset).limit(limit + 1)
    rows = list(db.scalars(query))
    has_more = len(rows) > limit
    rows = rows[:limit]
    data = []
    for customer in rows:
        purchases = list(db.scalars(select(Purchase).where(Purchase.customer_id == customer.id)))
        total = sum((p.total_amount for p in purchases if p.total_amount is not None and p.currency == "INR"), Decimal("0"))
        data.append({
            "customer_id": customer.id,
            "name": customer.name,
            "phone_masked": phone_mask_for(customer),
            "permissions": permission_map(db, customer.id),
            "purchases_count": len(purchases),
            "lifetime_value": {"amount": float(total), "currency": "INR"},
            "created_at": iso(customer.created_at),
        })
    return {"data": data, "pagination": {
        "limit": limit,
        "next_cursor": base64.urlsafe_b64encode(str(offset + len(rows)).encode("ascii")).decode("ascii").rstrip("=") if has_more else None,
    }}


@app.get("/api/jobs")
def jobs(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
    status: str | None = Query(default=None, pattern="^(queued|processing|accepted|simulated|delivered|failed|skipped)$"),
    campaign_id: str | None = None,
    job_type: str | None = Query(default=None, pattern="^(thank_you|promotional)$"),
    db: Session = Depends(get_db),
):
    offset = _cursor_offset(cursor)
    query = select(MessageJob)
    if status:
        query = query.where(MessageJob.status == status)
    if campaign_id:
        query = query.where(MessageJob.campaign_id == campaign_id)
    if job_type:
        query = query.where(MessageJob.job_type == job_type)
    query = query.order_by(MessageJob.created_at.desc(), MessageJob.id.desc()).offset(offset).limit(limit + 1)
    rows = list(db.scalars(query))
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {"data": [message_job_public(db, job) for job in rows], "pagination": {
        "limit": limit,
        "next_cursor": base64.urlsafe_b64encode(str(offset + len(rows)).encode("ascii")).decode("ascii").rstrip("=") if has_more else None,
    }}


@app.post("/api/campaigns/preview", status_code=201)
def campaign_preview(body: CampaignPreviewRequest, db: Session = Depends(get_db)):
    preview = build_campaign_message(
        body.product.name,
        body.product.category,
        body.product.available_stock,
        body.product.price,
        body.message,
    )
    campaign = Campaign(
        name=body.name,
        product_name=body.product.name,
        category=body.product.category,
        available_stock=body.product.available_stock,
        price=body.product.price,
        message_template_input=body.message,
        message_preview=preview,
        channel=CHANNEL,
        status="draft",
        created_at=utcnow(),
    )
    db.add(campaign)
    db.flush()
    eligible = eligible_campaign_customers(db, campaign)
    db.commit()
    return {"data": {
        "campaign_id": campaign.id,
        "status": campaign.status,
        "name": campaign.name,
        "product": {
            "name": campaign.product_name,
            "category": campaign.category,
            "available_stock": campaign.available_stock,
            "price": amount(campaign.price),
            "currency": "INR",
        },
        "channel": CHANNEL,
        "matching_opted_in_customer_count": len(eligible),
        "message_preview": campaign.message_preview,
        "sample_recipients": [{
            "customer_id": customer.id,
            "name": customer.name,
            "phone_masked": phone_mask_for(customer),
        } for customer in eligible[:5]],
        "created_at": iso(campaign.created_at),
    }}


@app.post("/api/campaigns/{campaign_id}/approve")
def approve_campaign(campaign_id: str, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    if campaign.status != "draft":
        raise HTTPException(status_code=409, detail="Only a draft campaign can be approved.")
    campaign.status = "approved"
    campaign.approved_at = utcnow()
    db.commit()
    return {"data": {"campaign_id": campaign.id, "status": campaign.status, "approved_at": iso(campaign.approved_at)}}


@app.post("/api/campaigns/{campaign_id}/simulate", status_code=202)
def simulate_campaign(
    campaign_id: str,
    body: SimulationRequest | None = None,
    db: Session = Depends(get_db),
):
    del body  # Outcome is deterministic; this endpoint performs no real delivery.
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    if campaign.status != "approved":
        raise HTTPException(status_code=409, detail="Only an approved campaign can be simulated.")
    now = utcnow()
    eligible = eligible_campaign_customers(db, campaign)
    created_jobs = []
    for customer in eligible:
        # Re-read the current permission immediately before job creation.
        if not has_permission(db, customer.id, "promotional"):
            continue
        job = MessageJob(
            job_type="promotional",
            channel=CHANNEL,
            status="queued",
            customer_id=customer.id,
            campaign_id=campaign.id,
            message_body=campaign.message_preview,
            scheduled_at=None,
            created_at=now,
        )
        db.add(job)
        created_jobs.append(job)
    campaign.status = "simulating" if created_jobs else "simulated"
    campaign.started_at = now
    if not created_jobs:
        campaign.completed_at = now
    db.commit()
    return {"data": {
        "campaign_id": campaign.id,
        "status": campaign.status,
        "channel": CHANNEL,
        "jobs_created": len(created_jobs),
        "jobs": [{
            "job_id": job.id,
            "customer_id": job.customer_id,
            "phone_masked": phone_mask_for(db.get(Customer, job.customer_id)),
            "job_type": job.job_type,
            "channel": CHANNEL,
            "status": job.status,
            "scheduled_at": None,
        } for job in created_jobs],
        "started_at": iso(now),
    }}
