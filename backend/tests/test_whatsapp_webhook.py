import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MessageJob
from app.webhook_only import app as webhook_only_app
from scripts import check_whatsapp_webhook


@pytest.fixture
def webhook_settings(monkeypatch):
    monkeypatch.setenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "fictional-verify-secret")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "fictional-meta-app-secret")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123456789012345")


def callback(message_id="wamid.mock-one", status="sent", timestamp="1793340000", *, phone_id="123456789012345", errors=None):
    status_data = {"id": message_id, "status": status, "timestamp": timestamp, "recipient_id": "919876543210"}
    if errors is not None:
        status_data["errors"] = errors
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": phone_id}, "statuses": [status_data],
    }}]}]}


def signed_post(client, payload, secret="fictional-meta-app-secret"):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/api/webhooks/whatsapp", content=raw, headers={
        "X-Hub-Signature-256": f"sha256={signature}", "Content-Type": "application/json",
    })


def accepted_job(client, purchase_request, *, mode="real_test", status="accepted", message_id="wamid.mock-one"):
    purchase = client.post("/api/purchases", json=purchase_request(permissions={"thank_you": False})).json()["data"]
    with SessionLocal() as db:
        job = MessageJob(
            job_type="thank_you", channel="whatsapp_real_test" if mode == "real_test" else "whatsapp_simulated",
            delivery_mode=mode, status=status, customer_id=purchase["customer_id"],
            purchase_id=purchase["purchase_id"], message_body="fictional test", provider_message_id=message_id,
            accepted_at=datetime(2026, 10, 29, tzinfo=timezone.utc),
        )
        db.add(job)
        db.commit()
        return job.id


def public_job(client, job_id):
    return next(row for row in client.get("/api/jobs").json()["data"] if row["job_id"] == job_id)


def test_get_challenge_exact_plain_text_and_invalid_tokens(client, webhook_settings, monkeypatch):
    params = {"hub.mode": "subscribe", "hub.verify_token": "fictional-verify-secret", "hub.challenge": "challenge-123"}
    response = client.get("/api/webhooks/whatsapp", params=params)
    assert response.status_code == 200 and response.text == "challenge-123"
    assert response.headers["content-type"].startswith("text/plain")
    assert "fictional-verify-secret" not in response.text
    assert client.get("/api/webhooks/whatsapp", params={**params, "hub.verify_token": "wrong"}).status_code == 403
    assert client.get("/api/webhooks/whatsapp", params={**params, "hub.mode": "other"}).status_code == 403
    assert client.get("/api/webhooks/whatsapp", params={k: v for k, v in params.items() if k != "hub.challenge"}).status_code == 403
    monkeypatch.delenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN")
    assert client.get("/api/webhooks/whatsapp", params=params).status_code == 403


def test_post_requires_signature_and_app_secret_before_mutation(client, purchase_request, webhook_settings, monkeypatch):
    job_id = accepted_job(client, purchase_request)
    raw = json.dumps(callback(status="delivered")).encode()
    assert client.post("/api/webhooks/whatsapp", content=raw).status_code == 403
    assert client.post("/api/webhooks/whatsapp", content=raw, headers={"X-Hub-Signature-256": "sha256=" + "0" * 64}).status_code == 403
    assert signed_post(client, callback(status="delivered"), secret="wrong").status_code == 403
    monkeypatch.delenv("WHATSAPP_APP_SECRET")
    assert signed_post(client, callback(status="delivered")).status_code == 403
    assert public_job(client, job_id)["status"] == "accepted"
    assert public_job(client, job_id)["delivery_status"] is None


def test_oversized_payload_is_rejected_without_mutation(client, purchase_request, webhook_settings):
    job_id = accepted_job(client, purchase_request)
    response = client.post("/api/webhooks/whatsapp", content=b"x" * 1_000_001)
    assert response.status_code == 413
    assert public_job(client, job_id)["delivery_status"] is None


def test_sent_is_not_delivered_then_delivered_is_confirmed(client, purchase_request, webhook_settings):
    job_id = accepted_job(client, purchase_request)
    assert signed_post(client, callback(status="sent")).status_code == 200
    sent = public_job(client, job_id)
    assert sent["status"] == "accepted" and sent["delivery_status"] == "sent"
    assert sent["delivered_at"] is None
    assert signed_post(client, callback(status="delivered", timestamp="1793340010")).status_code == 200
    delivered = public_job(client, job_id)
    assert delivered["status"] == "delivered" and delivered["delivery_status"] == "delivered"
    assert delivered["delivered_at"] == "2026-10-30T06:00:10Z"
    assert delivered["delivery_event_at"] == delivered["delivered_at"]
    assert delivered["provider_message_id"] == "wamid.mock-one"
    assert "919876543210" not in json.dumps(delivered)


def test_read_out_of_order_and_duplicates_do_not_regress(client, purchase_request, webhook_settings):
    job_id = accepted_job(client, purchase_request)
    assert signed_post(client, callback(status="read", timestamp="1793340020")).status_code == 200
    before = public_job(client, job_id)
    assert before["status"] == "delivered" and before["delivery_status"] == "read"
    assert signed_post(client, callback(status="read", timestamp="1793340020")).status_code == 200
    assert signed_post(client, callback(status="sent", timestamp="1793340000")).status_code == 200
    assert signed_post(client, callback(status="delivered", timestamp="1793340010")).status_code == 200
    after = public_job(client, job_id)
    assert after["delivery_status"] == "read"
    assert after["delivery_event_at"] == before["delivery_event_at"]
    assert after["state_version"] == before["state_version"]


def test_multiple_statuses_in_one_callback_follow_guarded_transitions(client, purchase_request, webhook_settings):
    job_id = accepted_job(client, purchase_request)
    payload = callback(status="sent")
    statuses = payload["entry"][0]["changes"][0]["value"]["statuses"]
    statuses.append({"id": "wamid.mock-one", "status": "delivered", "timestamp": "1793340010"})
    statuses.append({"id": "wamid.mock-one", "status": "read", "timestamp": "1793340020"})
    assert signed_post(client, payload).status_code == 200
    row = public_job(client, job_id)
    assert row["status"] == "delivered" and row["delivery_status"] == "read"
    assert row["state_version"] == 3


def test_failed_status_stores_only_sanitized_code(client, purchase_request, webhook_settings):
    job_id = accepted_job(client, purchase_request)
    payload = callback(status="failed", errors=[{
        "code": 131026, "title": "Private phone 919876543210 and token secret must not be saved",
    }])
    response = signed_post(client, payload)
    assert response.status_code == 200
    row = public_job(client, job_id)
    assert row["status"] == "failed" and row["delivery_status"] == "failed"
    assert row["reason"] == "meta_delivery_failed"
    assert row["delivery_failure_code"] == "131026"
    assert "919876543210" not in response.text + json.dumps(row)
    assert "Private phone" not in response.text + json.dumps(row)
    assert signed_post(client, callback(status="delivered")).status_code == 200
    assert public_job(client, job_id)["delivery_status"] == "failed"


def test_unknown_id_wrong_phone_and_unrelated_messages_are_ignored(client, purchase_request, webhook_settings):
    job_id = accepted_job(client, purchase_request)
    assert signed_post(client, callback(message_id="wamid.unknown", status="delivered")).status_code == 200
    assert signed_post(client, callback(status="delivered", phone_id="different-id")).status_code == 200
    inbound = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": "123456789012345"},
        "messages": [{"from": "919876543210", "text": {"body": "private inbound text"}}],
    }}]}]}
    assert signed_post(client, inbound).status_code == 200
    assert public_job(client, job_id)["status"] == "accepted"
    assert public_job(client, job_id)["delivery_status"] is None


def test_simulated_jobs_cannot_be_updated_by_signed_callback(client, purchase_request, webhook_settings):
    job_id = accepted_job(client, purchase_request, mode="simulation", status="simulated")
    assert signed_post(client, callback(status="delivered")).status_code == 200
    row = public_job(client, job_id)
    assert row["status"] == "simulated" and row["delivery_status"] is None


def test_webhooks_work_while_outgoing_automation_disabled(client, purchase_request, webhook_settings):
    job_id = accepted_job(client, purchase_request)
    assert signed_post(client, callback(status="delivered")).status_code == 200
    assert public_job(client, job_id)["delivery_status"] == "delivered"


def test_public_listener_exposes_only_webhook_routes(webhook_settings):
    with TestClient(webhook_only_app) as public:
        assert public.get("/api/jobs").status_code == 404
        assert public.get("/api/customers").status_code == 404
        assert public.get("/docs").status_code == 404
        assert public.get("/api/webhooks/whatsapp", params={
            "hub.mode": "subscribe", "hub.verify_token": "fictional-verify-secret", "hub.challenge": "hello",
        }).text == "hello"


def test_safe_local_check_script_uses_webhook_only_listener_without_outbound_http(webhook_settings, monkeypatch, capsys):
    with TestClient(webhook_only_app) as public:
        class LocalClient:
            def __enter__(self):
                return public

            def __exit__(self, *_args):
                return None

        monkeypatch.setattr(check_whatsapp_webhook.httpx, "Client", lambda **_kwargs: LocalClient())
        assert check_whatsapp_webhook.main([]) == 0
    output = capsys.readouterr().out
    assert "signed POST passed" in output
    assert "fictional-verify-secret" not in output
    assert "fictional-meta-app-secret" not in output
