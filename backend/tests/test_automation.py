import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import job_worker
from app import main as main_module
from app.db import SessionLocal
from app.models import MessageJob, Permission, Purchase, WhatsAppDeliveryAttempt
from app.main import app
from app.schemas import CreatePurchaseRequest
from app.services import as_utc, process_queued_jobs, purchase_fingerprint
from app.whatsapp import send_test_template
from scripts.queue_whatsapp_automation_test import main as queue_test


TEST_KEY = "wa-auto-test-001"
TEST_PHONE = "+919876543210"


@pytest.fixture
def real_test(monkeypatch):
    settings = {
        "WHATSAPP_AUTOMATION_MODE": "real_test",
        "WHATSAPP_AUTOMATION_ENABLED": "true",
        "WHATSAPP_AUTOMATION_PURCHASE_KEY": TEST_KEY,
        "WHATSAPP_MODE": "test",
        "WHATSAPP_ACCESS_TOKEN": "fake-provider-secret",
        "WHATSAPP_PHONE_NUMBER_ID": "123456789012345",
        "WHATSAPP_GRAPH_API_VERSION": "v26.0",
        "WHATSAPP_TEST_RECIPIENT": TEST_PHONE,
        "WHATSAPP_TEST_ALLOWED_RECIPIENTS": TEST_PHONE,
        "WHATSAPP_TEST_RECIPIENT_VERIFIED": "true",
        "WHATSAPP_TEST_TEMPLATE_NAME": "approved_order_test",
        "WHATSAPP_TEST_TEMPLATE_LANGUAGE": "en_US",
        "WHATSAPP_TEST_TEMPLATE_COMPONENTS": json.dumps([
            {"type": "body", "parameters": [{"type": "text", "text": "Fictional test"}]}
        ]),
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    return settings


def create_job(client, purchase_request, *, phone=TEST_PHONE, key=TEST_KEY):
    response = client.post("/api/purchases", json=purchase_request(
        phone=phone, key=key, permissions={"thank_you": True},
    ))
    assert response.status_code == 201
    data = response.json()["data"]
    with SessionLocal() as db:
        job = db.get(MessageJob, data["thank_you_job"]["job_id"])
        due = as_utc(job.scheduled_at)
    return data, due


def run_worker(now, sender):
    with SessionLocal() as db:
        return process_queued_jobs(db, now=now, sender=sender)


def duplicate_job(job_id):
    with SessionLocal() as db:
        job = db.get(MessageJob, job_id)
        copy = MessageJob(
            job_type=job.job_type, channel=job.channel, status="queued",
            delivery_mode=job.delivery_mode, customer_id=job.customer_id,
            purchase_id=job.purchase_id, message_body=job.message_body,
            scheduled_at=job.scheduled_at,
        )
        db.add(copy)
        db.commit()
        return copy.id


def test_due_job_uses_approved_template_and_records_acceptance(client, purchase_request, real_test):
    data, due = create_job(client, purchase_request)
    job_id = data["thank_you_job"]["job_id"]
    assert data["thank_you_job"]["channel"] == "whatsapp_real_test"
    calls = []

    def provider(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"messages": [{"id": "wamid.automation-accepted"}]})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        sender = lambda config: send_test_template(config, http_client=http)
        assert run_worker(due - timedelta(seconds=1), sender) == 0
        assert calls == []
        assert run_worker(due, sender) == 1
        assert run_worker(due + timedelta(minutes=1), sender) == 0

    assert len(calls) == 1
    assert calls[0]["to"] == TEST_PHONE[1:]
    assert calls[0]["template"] == {
        "name": "approved_order_test", "language": {"code": "en_US"},
        "components": json.loads(real_test["WHATSAPP_TEST_TEMPLATE_COMPONENTS"]),
    }
    jobs = client.get("/api/jobs", params={"status": "accepted"}).json()["data"]
    assert jobs[0]["job_id"] == job_id
    assert jobs[0]["provider_message_id"] == "wamid.automation-accepted"
    assert jobs[0]["state_version"] == 2
    assert jobs[0]["accepted_at"] is not None
    assert jobs[0]["delivered_at"] is None
    assert jobs[0]["completed_at"] is None
    assert TEST_PHONE not in json.dumps(jobs)
    assert client.get("/api/jobs", params={"status": "delivered"}).json()["data"] == []
    assert client.get("/api/dashboard").json()["data"]["message_jobs"]["accepted"] == 1
    with SessionLocal() as db:
        assert "not a production thank-you" in db.get(MessageJob, job_id).message_body
        assert db.scalar(select(WhatsAppDeliveryAttempt)).provider_message_id == "wamid.automation-accepted"


def test_default_mode_remains_simulation(client, purchase_request):
    data, due = create_job(client, purchase_request)
    sender = Mock()
    run_worker(due, sender)
    sender.assert_not_called()
    assert data["thank_you_job"]["delivery_mode"] == "simulation"
    assert client.get("/api/jobs").json()["data"][0]["status"] == "simulated"


def test_requested_real_test_rejects_missing_server_settings_instead_of_simulating(client, purchase_request):
    body = purchase_request(key=TEST_KEY, permissions={"thank_you": True})
    body["delivery_mode"] = "real_test"
    response = client.post("/api/purchases", json=body)
    assert response.status_code == 409
    assert "real_test_disabled" in response.json()["error"]["message"]
    assert TEST_PHONE not in response.text
    assert client.get("/api/dashboard").json()["data"]["purchases_total"] == 0
    assert client.get("/api/jobs").json()["data"] == []


@pytest.mark.parametrize("setting,value,expected", [
    ("WHATSAPP_AUTOMATION_PURCHASE_KEY", "different-key", "test_purchase_not_selected"),
    ("WHATSAPP_TEST_RECIPIENT_VERIFIED", "false", "invalid_real_test_configuration"),
    ("WHATSAPP_TEST_ALLOWED_RECIPIENTS", "+919876543211", "invalid_real_test_configuration"),
    ("WHATSAPP_AUTOMATION_ENABLED", "false", "real_test_disabled"),
])
def test_explicit_real_test_checks_server_configuration_before_purchase(client, purchase_request, real_test, monkeypatch, setting, value, expected):
    monkeypatch.setenv(setting, value)
    body = purchase_request(key=TEST_KEY, permissions={"thank_you": True})
    body["delivery_mode"] = "real_test"
    response = client.post("/api/purchases", json=body)
    assert response.status_code == 409
    assert expected in response.json()["error"]["message"]
    assert client.get("/api/dashboard").json()["data"]["purchases_total"] == 0


def test_explicit_real_test_must_match_full_verified_number_and_record_consent(client, purchase_request, real_test):
    body = purchase_request(phone="+918765433210", key=TEST_KEY, permissions={"thank_you": True})
    body["delivery_mode"] = "real_test"
    response = client.post("/api/purchases", json=body)
    assert response.status_code == 409
    assert "recipient_not_allowlisted" in response.json()["error"]["message"]
    body = purchase_request(key=TEST_KEY, permissions={"thank_you": False})
    body["delivery_mode"] = "real_test"
    response = client.post("/api/purchases", json=body)
    assert response.status_code == 409
    assert "thank_you_permission_missing" in response.json()["error"]["message"]


def test_explicit_real_test_replay_is_idempotent(client, purchase_request, real_test):
    body = purchase_request(key=TEST_KEY, permissions={"thank_you": True})
    body["delivery_mode"] = "real_test"
    first = client.post("/api/purchases", json=body)
    again = client.post("/api/purchases", json=body)
    assert first.status_code == again.status_code == 201
    assert first.json()["data"]["purchase_id"] == again.json()["data"]["purchase_id"]
    assert first.json()["data"]["thank_you_job"]["delivery_mode"] == "real_test"


def test_legacy_purchase_fingerprint_and_replay_are_unchanged(client, purchase_request):
    body = purchase_request(key="legacy-sale", permissions={"thank_you": True})
    model = CreatePurchaseRequest.model_validate(body)
    legacy_fingerprint = purchase_fingerprint(model.model_dump(mode="json", exclude={"idempotency_key", "delivery_mode"}))
    first = client.post("/api/purchases", json=body)
    second = client.post("/api/purchases", json=body)
    assert first.status_code == second.status_code == 201
    assert first.json()["data"]["purchase_id"] == second.json()["data"]["purchase_id"]
    with SessionLocal() as db:
        assert db.get(Purchase, first.json()["data"]["purchase_id"]).request_fingerprint == legacy_fingerprint


def test_existing_simulation_purchase_cannot_be_promoted_into_a_real_test(client, purchase_request, monkeypatch):
    body = purchase_request(key=TEST_KEY, permissions={"thank_you": True})
    first = client.post("/api/purchases", json=body)
    assert first.json()["data"]["thank_you_job"]["delivery_mode"] == "simulation"
    monkeypatch.setenv("WHATSAPP_AUTOMATION_MODE", "real_test")
    monkeypatch.setenv("WHATSAPP_AUTOMATION_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_AUTOMATION_PURCHASE_KEY", TEST_KEY)
    body["delivery_mode"] = "real_test"
    assert client.post("/api/purchases", json=body).status_code == 409
    assert client.get("/api/dashboard").json()["data"]["purchases_total"] == 1


def test_startup_reports_effective_worker_mode_without_secrets(real_test, clean_database, caplog):
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with TestClient(app):
            pass
    assert "automation_mode=real_test" in caplog.text
    assert "real_test_enabled=True" in caplog.text
    assert "test_key_configured=True" in caplog.text
    assert TEST_PHONE not in caplog.text
    assert "fake-provider-secret" not in caplog.text


def test_worker_logs_only_error_type_not_sensitive_exception(monkeypatch, caplog):
    class StopAfterOneIteration:
        done = False

        def is_set(self):
            return self.done

        def wait(self, _interval):
            self.done = True

    def fail(_db):
        raise RuntimeError("fake-provider-secret " + TEST_PHONE)

    monkeypatch.setattr(main_module, "_worker_stop", StopAfterOneIteration())
    monkeypatch.setattr(main_module, "process_queued_jobs", fail)
    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        main_module._worker_loop()
    assert "RuntimeError" in caplog.text
    assert TEST_PHONE not in caplog.text
    assert "fake-provider-secret" not in caplog.text


def test_legacy_simulation_worker_cannot_overwrite_real_test_job(client, purchase_request, real_test):
    data, due = create_job(client, purchase_request)
    job_id = data["thank_you_job"]["job_id"]
    with SessionLocal() as old_worker:
        job = old_worker.get(MessageJob, job_id)
        job.status = "simulated"  # Legacy processor does not know about state_version.
        with pytest.raises(IntegrityError):
            old_worker.commit()
        old_worker.rollback()
    sender = Mock(return_value="wamid.real-test")
    run_worker(due, sender)
    sender.assert_called_once()
    with SessionLocal() as stale_worker:
        job = stale_worker.get(MessageJob, job_id)
        assert job.status == "accepted" and job.state_version == 2
        job.status = "simulated"
        with pytest.raises(IntegrityError):
            stale_worker.commit()
        stale_worker.rollback()
    assert client.get("/api/jobs").json()["data"][0]["status"] == "accepted"


def test_worker_does_not_send_if_claim_was_lost_before_dispatch(client, purchase_request, real_test, monkeypatch):
    data, due = create_job(client, purchase_request)
    original = job_worker._claim

    def claim_then_change_status(*args):
        result = original(*args)
        with SessionLocal() as db:
            job = db.get(MessageJob, data["thank_you_job"]["job_id"])
            job.status = "skipped"
            job.state_version += 1
            job.reason = "manual_test_stop"
            db.commit()
        return result

    monkeypatch.setattr(job_worker, "_claim", claim_then_change_status)
    sender = Mock()
    run_worker(due, sender)
    sender.assert_not_called()
    assert client.get("/api/jobs").json()["data"][0]["status"] == "skipped"


def test_nonselected_purchase_remains_simulated(client, purchase_request, real_test):
    data, due = create_job(client, purchase_request, key="ordinary-sale")
    sender = Mock()
    run_worker(due, sender)
    sender.assert_not_called()
    assert data["thank_you_job"]["delivery_mode"] == "simulation"


def test_enabling_test_mode_never_promotes_existing_simulation_jobs(client, purchase_request, monkeypatch):
    data, due = create_job(client, purchase_request)
    monkeypatch.setenv("WHATSAPP_AUTOMATION_MODE", "real_test")
    monkeypatch.setenv("WHATSAPP_AUTOMATION_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_AUTOMATION_PURCHASE_KEY", TEST_KEY)
    sender = Mock()
    run_worker(due, sender)
    sender.assert_not_called()
    with SessionLocal() as db:
        assert db.get(MessageJob, data["thank_you_job"]["job_id"]).status == "simulated"


def test_revoked_consent_prevents_dispatch(client, purchase_request, real_test):
    data, due = create_job(client, purchase_request)
    with SessionLocal() as db:
        permission = db.scalar(select(Permission).where(
            Permission.customer_id == data["customer_id"], Permission.permission_type == "thank_you",
        ))
        permission.allowed = False
        db.commit()
    sender = Mock()
    run_worker(due, sender)
    sender.assert_not_called()
    assert client.get("/api/jobs").json()["data"][0]["reason"] == "thank_you_permission_missing"


def test_consent_rechecked_after_durable_claim(client, purchase_request, real_test, monkeypatch):
    data, due = create_job(client, purchase_request)
    original = job_worker._claim

    def claim_then_revoke(*args):
        result = original(*args)
        with SessionLocal() as db:
            permission = db.scalar(select(Permission).where(
                Permission.customer_id == data["customer_id"], Permission.permission_type == "thank_you",
            ))
            permission.allowed = False
            db.commit()
        return result

    monkeypatch.setattr(job_worker, "_claim", claim_then_revoke)
    sender = Mock()
    run_worker(due, sender)
    sender.assert_not_called()
    assert client.get("/api/jobs").json()["data"][0]["status"] == "skipped"


def test_same_last_four_digits_do_not_authorize_other_customer(client, purchase_request, real_test):
    _, due = create_job(client, purchase_request, phone="+918765433210")
    sender = Mock()
    run_worker(due, sender)
    sender.assert_not_called()
    assert client.get("/api/jobs").json()["data"][0]["reason"] == "recipient_not_allowlisted"


@pytest.mark.parametrize("setting,value", [
    ("WHATSAPP_AUTOMATION_ENABLED", "false"),
    ("WHATSAPP_AUTOMATION_MODE", "simulation"),
    ("WHATSAPP_TEST_RECIPIENT_VERIFIED", "false"),
    ("WHATSAPP_TEST_ALLOWED_RECIPIENTS", "+919876543211"),
    ("WHATSAPP_MODE", "live"),
    ("WHATSAPP_AUTOMATION_PURCHASE_KEY", "another-test"),
])
def test_send_time_gates_fail_closed(client, purchase_request, real_test, monkeypatch, setting, value):
    _, due = create_job(client, purchase_request)
    monkeypatch.setenv(setting, value)
    sender = Mock()
    run_worker(due, sender)
    sender.assert_not_called()
    assert client.get("/api/jobs").json()["data"][0]["status"] == "skipped"


def test_duplicate_rows_and_restarts_do_not_repeat_send(client, purchase_request, real_test):
    data, due = create_job(client, purchase_request)
    duplicate_job(data["thank_you_job"]["job_id"])
    sender = Mock(return_value="wamid.once")
    run_worker(due, sender)
    run_worker(due + timedelta(days=1), sender)
    duplicate_job(data["thank_you_job"]["job_id"])
    run_worker(due + timedelta(days=1), sender)
    sender.assert_called_once()
    with SessionLocal() as db:
        assert len(list(db.scalars(select(WhatsAppDeliveryAttempt)))) == 1
    statuses = [row["status"] for row in client.get("/api/jobs").json()["data"]]
    assert sorted(statuses) == ["accepted", "skipped", "skipped"]


def test_concurrent_workers_have_only_one_provider_attempt(client, purchase_request, real_test):
    data, due = create_job(client, purchase_request)
    duplicate_job(data["thank_you_job"]["job_id"])
    started, release = threading.Event(), threading.Event()
    calls = []

    def sender(_config):
        calls.append(1)
        started.set()
        assert release.wait(5)
        return "wamid.concurrent"

    with SessionLocal() as legacy:
        stale_job = legacy.get(MessageJob, data["thank_you_job"]["job_id"])
        assert stale_job.status == "queued"
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run_worker, due, sender)
            assert started.wait(5)
            try:
                stale_job.status = "simulated"
                with pytest.raises(IntegrityError):
                    legacy.commit()
                legacy.rollback()
                second = pool.submit(run_worker, due, sender)
                second.result(timeout=5)
            finally:
                release.set()
            first.result(timeout=5)
    assert len(calls) == 1


def test_interrupted_dispatch_is_not_retried_on_restart(client, purchase_request, real_test):
    data, due = create_job(client, purchase_request)
    crash_sender = Mock(side_effect=SystemExit("simulated process interruption"))
    with pytest.raises(SystemExit):
        run_worker(due, crash_sender)
    duplicate_job(data["thank_you_job"]["job_id"])
    later_sender = Mock()
    run_worker(due + timedelta(hours=1), later_sender)
    later_sender.assert_not_called()
    with SessionLocal() as db:
        job = db.get(MessageJob, data["thank_you_job"]["job_id"])
        assert job.status == "processing"
        assert job.reason == "provider_outcome_unconfirmed_no_retry"


def test_failure_saving_acceptance_does_not_resend(client, purchase_request, real_test, monkeypatch):
    data, due = create_job(client, purchase_request)
    sender = Mock(return_value="wamid.accepted-before-database-error")
    original_commit = Session.commit

    def fail_acceptance_commit(session):
        if any(isinstance(row, MessageJob) and row.status == "accepted" for row in session.dirty):
            raise RuntimeError("simulated disk failure")
        return original_commit(session)

    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", fail_acceptance_commit)
        with pytest.raises(RuntimeError, match="simulated disk failure"):
            run_worker(due, sender)
    run_worker(due + timedelta(minutes=1), sender)
    sender.assert_called_once()
    with SessionLocal() as db:
        assert db.get(MessageJob, data["thank_you_job"]["job_id"]).status == "processing"
        assert db.scalar(select(WhatsAppDeliveryAttempt)).status == "processing"


@pytest.mark.parametrize("failure,expected_reason", [
    ("timeout", "meta_timeout_outcome_unknown"),
    ("network", "meta_network_outcome_unknown"),
    ("server_error", "meta_server_error_outcome_unknown"),
    ("missing_id", "meta_response_missing_id_outcome_unknown"),
    ("invalid_token", "meta_auth_invalid_or_expired"),
])
def test_provider_failures_are_durable_and_never_auto_retried(client, purchase_request, real_test, failure, expected_reason, caplog):
    data, due = create_job(client, purchase_request)
    calls = []

    def provider(_request):
        calls.append(1)
        if failure == "timeout":
            raise httpx.ReadTimeout("fake-provider-secret " + TEST_PHONE)
        if failure == "network":
            raise httpx.ConnectError("fake-provider-secret " + TEST_PHONE)
        if failure == "missing_id":
            return httpx.Response(200, json={})
        return httpx.Response(503 if failure == "server_error" else 400, json={
            "error": {"code": 190 if failure == "invalid_token" else 2, "message": TEST_PHONE},
        })

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        sender = lambda config: send_test_template(config, http_client=http)
        run_worker(due, sender)
        duplicate_job(data["thank_you_job"]["job_id"])
        run_worker(due + timedelta(hours=1), sender)
    assert len(calls) == 1
    with SessionLocal() as db:
        job = db.get(MessageJob, data["thank_you_job"]["job_id"])
        assert job.status == "failed"
        assert job.reason == expected_reason
        assert job.provider_message_id is None
    assert TEST_PHONE not in caplog.text
    assert "fake-provider-secret" not in caplog.text


def test_queue_cli_is_explicit_private_and_idempotent(client, real_test, capsys):
    assert queue_test([], api_client=client) == 0
    assert queue_test(["--queue"], api_client=client) == 2
    assert client.get("/api/dashboard").json()["data"]["purchases_total"] == 0
    for _ in range(2):
        assert queue_test(["--queue", "--consent-thank-you"], api_client=client) == 0
    assert client.get("/api/dashboard").json()["data"]["purchases_total"] == 1
    output = capsys.readouterr()
    assert TEST_PHONE not in output.out + output.err
    assert "fake-provider-secret" not in output.out + output.err


def test_queue_cli_explains_safe_server_side_configuration_rejection(real_test, capsys):
    class RejectedBackend:
        def post(self, *_args, **_kwargs):
            return httpx.Response(409, json={"error": {"message": "Real-test request rejected: real_test_disabled."}})

    assert queue_test(["--queue", "--consent-thank-you"], api_client=RejectedBackend()) == 1
    output = capsys.readouterr()
    assert "real_test_disabled" in output.err
    assert TEST_PHONE not in output.out + output.err
    assert "fake-provider-secret" not in output.out + output.err


def test_campaigns_continue_simulating_when_real_tests_enabled(client, purchase_request, real_test):
    client.post("/api/purchases", json=purchase_request(key="ordinary-sale", permissions={"promotional": True}))
    campaign = client.post("/api/campaigns/preview", json={
        "name": "Kurtis", "product": {"name": "Test kurti", "category": "Kurtis", "available_stock": 1},
        "message": "Fictional test", "channel": "whatsapp_simulated",
    }).json()["data"]["campaign_id"]
    client.post(f"/api/campaigns/{campaign}/approve")
    client.post(f"/api/campaigns/{campaign}/simulate")
    from app.models import utcnow
    sender = Mock()
    run_worker(utcnow(), sender)
    sender.assert_not_called()
    assert client.get("/api/jobs").json()["data"][0]["status"] == "simulated"
    assert client.get("/api/dashboard").json()["data"]["campaigns"]["simulated"] == 1
