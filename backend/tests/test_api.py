from datetime import datetime

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Customer


def _post_purchase(client: TestClient, body: dict):
    return client.post("/api/purchases", json=body)


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["data"]["database"] == "ok"


def test_purchase_normalizes_phone_masks_response_and_records_permissions(client, purchase_request):
    body = purchase_request(
        permissions={"thank_you": True, "promotional": False},
        total_amount=1499,
        currency="INR",
    )
    response = _post_purchase(client, body)
    assert response.status_code == 201
    payload = response.json()["data"]
    serialized = response.text
    assert payload["customer"]["phone_masked"] == "******3210"
    assert "9876543210" not in serialized
    assert "+919876543210" not in serialized
    assert payload["purchase"]["items"][0]["category"] == "Kurtis"
    assert payload["customer"]["permissions"]["thank_you"]["allowed"] is True
    assert payload["customer"]["permissions"]["thank_you"]["choice_recorded"] is True
    assert payload["customer"]["permissions"]["promotional"]["allowed"] is False
    assert payload["customer"]["permissions"]["promotional"]["choice_recorded"] is True
    assert payload["thank_you_job"]["channel"] == "whatsapp_simulated"
    assert payload["thank_you_job"]["status"] == "queued"
    assert payload["thank_you_job"]["scheduled_at"]
    with SessionLocal() as db:
        customer = db.get(Customer, payload["customer_id"])
        assert "9876543210" not in customer.phone_ciphertext


def test_missing_consent_defaults_false_unrecorded_and_creates_no_job(client, purchase_request):
    response = _post_purchase(client, purchase_request())
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["thank_you_job"] is None
    for kind in ("thank_you", "promotional"):
        assert data["customer"]["permissions"][kind] == {
            "allowed": False,
            "choice_recorded": False,
            "recorded_at": None,
        }


def test_repeat_phone_reuses_customer_and_preserves_recorded_choice(client, purchase_request):
    first = _post_purchase(client, purchase_request(permissions={"promotional": True})).json()["data"]
    second = _post_purchase(client, purchase_request(phone="+919876543210")).json()["data"]
    assert second["customer_id"] == first["customer_id"]
    assert second["purchase_id"] != first["purchase_id"]
    assert second["customer"]["permissions"]["promotional"]["allowed"] is True
    assert second["customer"]["permissions"]["promotional"]["choice_recorded"] is True
    assert second["customer"]["permissions"]["thank_you"]["allowed"] is False


def test_thank_you_due_time_uses_configured_delay(client, purchase_request, monkeypatch):
    monkeypatch.setenv("THANK_YOU_DELAY_SECONDS", "120")
    data = _post_purchase(client, purchase_request(permissions={"thank_you": True})).json()["data"]
    created = datetime.fromisoformat(data["purchase"]["purchased_at"].replace("Z", "+00:00"))
    scheduled = datetime.fromisoformat(data["thank_you_job"]["scheduled_at"].replace("Z", "+00:00"))
    assert (scheduled - created).total_seconds() == 120


def test_idempotency_prevents_duplicate_and_rejects_changed_body(client, purchase_request):
    body = purchase_request(permissions={"thank_you": False}, key="sale-101")
    first = _post_purchase(client, body)
    replay = _post_purchase(client, body)
    assert first.status_code == replay.status_code == 201
    assert replay.json()["data"]["purchase_id"] == first.json()["data"]["purchase_id"]
    changed = purchase_request(category="Sarees", permissions={"thank_you": False}, key="sale-101")
    assert _post_purchase(client, changed).status_code == 409
    assert client.get("/api/dashboard").json()["data"]["purchases_total"] == 1


def test_invalid_phone_and_multiple_line_items_are_validation_errors(client, purchase_request):
    invalid_phone = purchase_request(phone="555-1234")
    response = _post_purchase(client, invalid_phone)
    assert response.status_code == 422
    assert "555-1234" not in response.text
    multi = purchase_request()
    multi["purchase"]["items"].append({"name": "Second", "category": "Shirts"})
    assert _post_purchase(client, multi).status_code == 422


def test_customer_search_returns_only_a_masked_phone(client, purchase_request):
    _post_purchase(client, purchase_request(permissions={"thank_you": False}))
    response = client.get("/api/customers", params={"search": "9876543210"})
    assert response.status_code == 200
    assert response.json()["data"][0]["phone_masked"] == "******3210"
    assert "9876543210" not in response.text


def test_phone_numbers_are_not_echoed_from_other_text_fields(client, purchase_request):
    body = purchase_request()
    body["customer"]["name"] = "Call 9876543210"
    response = _post_purchase(client, body)
    assert response.status_code == 422
    assert "9876543210" not in response.text
    campaign_response = client.post("/api/campaigns/preview", json={
        "name": "Test",
        "product": {"name": "Blue kurti", "category": "Kurtis", "available_stock": 1},
        "message": "Call 9876543210",
        "channel": "whatsapp_simulated",
    })
    assert campaign_response.status_code == 422
    assert "9876543210" not in campaign_response.text


def test_campaign_category_matching_opt_out_and_template_facts(client, purchase_request):
    _post_purchase(client, purchase_request(permissions={"promotional": True}))
    _post_purchase(client, purchase_request(phone="9876543211", category="Sarees", permissions={"promotional": True}))
    _post_purchase(client, purchase_request(phone="9876543212", category="Kurtis", permissions={"promotional": False}))
    body = {
        "name": "New kurti stock",
        "product": {"name": "Indigo kurti", "category": "Kurtis", "available_stock": 4, "price": 1599},
        "message": "Visit us this week.",
        "channel": "whatsapp_simulated",
    }
    response = client.post("/api/campaigns/preview", json=body)
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["matching_opted_in_customer_count"] == 1
    assert data["product"]["available_stock"] == 4
    assert data["product"]["price"] == 1599
    assert "Indigo kurti" in data["message_preview"]
    assert "4 in stock" in data["message_preview"]
    assert "Price: INR 1599.00" in data["message_preview"]
    assert data["channel"] == "whatsapp_simulated"
    assert all(row["phone_masked"] == "******3210" for row in data["sample_recipients"])


def test_campaign_requires_approval_and_simulation_is_synthetic(client, purchase_request):
    _post_purchase(client, purchase_request(permissions={"promotional": True}))
    preview = client.post("/api/campaigns/preview", json={
        "name": "Kurtis",
        "product": {"name": "Blue kurti", "category": "Kurtis", "available_stock": 2},
        "message": "See it in store.",
        "channel": "whatsapp_simulated",
    }).json()["data"]
    campaign_id = preview["campaign_id"]
    assert client.post(f"/api/campaigns/{campaign_id}/simulate", json={}).status_code == 409
    approved = client.post(f"/api/campaigns/{campaign_id}/approve")
    assert approved.status_code == 200
    simulated = client.post(f"/api/campaigns/{campaign_id}/simulate", json={}).json()["data"]
    assert simulated["status"] == "simulating"
    assert simulated["jobs_created"] == 1
    assert simulated["jobs"][0]["channel"] == "whatsapp_simulated"
    assert simulated["jobs"][0]["status"] == "queued"
    jobs = client.get("/api/jobs").json()["data"]
    assert len(jobs) == 1 and jobs[0]["job_type"] == "promotional"
    assert client.post(f"/api/campaigns/{campaign_id}/simulate", json={}).status_code == 409
    second_preview = client.post("/api/campaigns/preview", json={
        "name": "Kurtis follow-up",
        "product": {"name": "Blue kurti", "category": "Kurtis", "available_stock": 2},
        "message": "See it in store.",
        "channel": "whatsapp_simulated",
    }).json()["data"]
    assert second_preview["matching_opted_in_customer_count"] == 0


def test_simulation_worker_records_only_synthetic_outcomes(client, purchase_request):
    _post_purchase(client, purchase_request(permissions={"thank_you": True}))
    with SessionLocal() as db:
        from app.services import process_queued_jobs
        from app.models import MessageJob, utcnow
        job = db.query(MessageJob).one()
        process_queued_jobs(db, now=job.scheduled_at)
        db.refresh(job)
        assert job.status == "simulated"
        assert job.completed_at is not None
