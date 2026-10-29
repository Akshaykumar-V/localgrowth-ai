import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from string import Template

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .models import Campaign, Customer, MessageJob, Permission, Purchase, PurchaseItem, utcnow
from .security import phone_lookup_hash


PERMISSION_TYPES = ("thank_you", "promotional")
CHANNEL = "whatsapp_simulated"


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    value = as_utc(value)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z") if value else None


def amount(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def permission_map(db: Session, customer_id: str) -> dict[str, dict]:
    records = {
        record.permission_type: record
        for record in db.scalars(select(Permission).where(Permission.customer_id == customer_id))
    }
    result = {}
    for kind in PERMISSION_TYPES:
        record = records.get(kind)
        result[kind] = {
            "allowed": bool(record.allowed) if record else False,
            "choice_recorded": bool(record.choice_recorded) if record else False,
            "recorded_at": iso(record.recorded_at) if record else None,
        }
    return result


def _permission_record(db: Session, customer_id: str, kind: str) -> Permission | None:
    return db.scalar(
        select(Permission).where(
            Permission.customer_id == customer_id,
            Permission.permission_type == kind,
        )
    )


def has_permission(db: Session, customer_id: str, kind: str) -> bool:
    record = _permission_record(db, customer_id, kind)
    return bool(record and record.choice_recorded and record.allowed)


def phone_mask_for(customer: Customer) -> str:
    return "******" + customer.phone_last4


def build_thank_you_message(customer: Customer, item: PurchaseItem) -> str:
    return Template(
        "Thank you, $customer_name, for purchasing $product_name from $category. We appreciate your visit."
    ).substitute(customer_name=customer.name, product_name=item.name, category=item.category)


def build_campaign_message(product_name: str, category: str, stock: int, price: Decimal | None, copy: str) -> str:
    if stock == 0:
        stock_text = "$product_name ($category) is currently out of stock."
    else:
        stock_count = "Only 1 in stock" if stock == 1 else "$stock in stock"
        stock_text = f"$product_name ($category) is in stock. {stock_count}."
    parts = [Template(stock_text).substitute(product_name=product_name, category=category, stock=stock)]
    if price is not None:
        parts.append(Template("Price: INR $price.").substitute(price=f"{price:.2f}"))
    if copy:
        parts.append(Template("$copy").substitute(copy=copy))
    return " ".join(parts)


def purchase_fingerprint(request_data: dict) -> str:
    canonical = json.dumps(request_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_purchase_response(db: Session, purchase: Purchase) -> dict:
    purchase = db.scalar(
        select(Purchase)
        .options(selectinload(Purchase.items), selectinload(Purchase.customer))
        .where(Purchase.id == purchase.id)
    )
    customer = purchase.customer
    job = db.scalar(
        select(MessageJob).where(
            MessageJob.purchase_id == purchase.id,
            MessageJob.job_type == "thank_you",
        )
    )
    return {
        "customer_id": customer.id,
        "purchase_id": purchase.id,
        "customer": {
            "name": customer.name,
            "phone_masked": phone_mask_for(customer),
            "permissions": permission_map(db, customer.id),
        },
        "purchase": {
            "total_amount": amount(purchase.total_amount),
            "currency": purchase.currency,
            "items": [
                {
                    "name": item.name,
                    "category": item.category,
                    "quantity": item.quantity,
                    "unit_price": amount(item.unit_price),
                }
                for item in purchase.items
            ],
            "purchased_at": iso(purchase.purchased_at),
        },
        "thank_you_job": message_job_public(db, job) if job else None,
    }


def message_job_public(db: Session, job: MessageJob) -> dict:
    customer = db.get(Customer, job.customer_id)
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "channel": job.channel,
        "status": job.status,
        "customer_id": job.customer_id,
        "phone_masked": phone_mask_for(customer),
        "purchase_id": job.purchase_id,
        "campaign_id": job.campaign_id,
        "scheduled_at": iso(job.scheduled_at),
        "reason": job.reason,
        "created_at": iso(job.created_at),
        "completed_at": iso(job.completed_at),
        "delivery_mode": job.delivery_mode,
        "state_version": job.state_version,
        "provider_message_id": job.provider_message_id,
        "processing_started_at": iso(job.processing_started_at),
        "accepted_at": iso(job.accepted_at),
        "delivered_at": iso(job.delivered_at),
        "delivery_status": job.delivery_status,
        "delivery_event_at": iso(job.delivery_event_at),
        "delivery_failure_code": job.delivery_failure_code,
        "test_notice": (
            "Approved template test only; this is not a production thank-you template."
            if job.delivery_mode == "real_test" else None
        ),
    }


def eligible_campaign_customers(db: Session, campaign_or_category: Campaign | str) -> list[Customer]:
    category = campaign_or_category.category if isinstance(campaign_or_category, Campaign) else campaign_or_category
    suppression_hours = max(0, int(os.getenv("PROMOTIONAL_SUPPRESSION_HOURS", "168")))
    cutoff = utcnow() - timedelta(hours=suppression_hours)
    recent_job_customers = select(MessageJob.customer_id).where(
        MessageJob.job_type == "promotional",
        MessageJob.created_at >= cutoff,
        MessageJob.status.in_(("queued", "simulated")),
    )
    query = (
        select(Customer)
        .join(Purchase, Purchase.customer_id == Customer.id)
        .join(PurchaseItem, PurchaseItem.purchase_id == Purchase.id)
        .join(Permission, Permission.customer_id == Customer.id)
        .where(
            func.lower(PurchaseItem.category) == category.lower(),
            Permission.permission_type == "promotional",
            Permission.choice_recorded.is_(True),
            Permission.allowed.is_(True),
            Customer.id.not_in(recent_job_customers),
        )
        .distinct()
        .order_by(Customer.created_at, Customer.id)
    )
    return list(db.scalars(query))


def process_queued_jobs(db: Session, now: datetime | None = None, *, sender=None) -> int:
    # Keep the existing worker entry point for callers and simulation tests.
    from .job_worker import process_queued_jobs as process
    return process(db, now, sender=sender)
