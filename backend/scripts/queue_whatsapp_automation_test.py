"""Queue one synthetic purchase using the configured verified test recipient."""

import argparse
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.job_worker import real_test_enabled  # noqa: E402
from app.whatsapp import WhatsAppConfigurationError, WhatsAppTestConfig  # noqa: E402

_SAFE_REJECTIONS = {
    "real_test_disabled", "test_purchase_not_selected", "thank_you_permission_missing",
    "recipient_not_allowlisted", "invalid_real_test_configuration",
}


def main(argv=None, *, api_client=None) -> int:
    parser = argparse.ArgumentParser(description="Queue one controlled automatic template test on the local backend.")
    parser.add_argument("--queue", action="store_true")
    parser.add_argument("--consent-thank-you", action="store_true", help="Record your explicit thank-you consent for this test.")
    args = parser.parse_args(argv)
    if not args.queue:
        print("Dry run only. Use --queue --consent-thank-you to schedule one test purchase.")
        return 0
    if not args.consent_thank_you:
        print("Explicit --consent-thank-you is required for your own verified recipient.", file=sys.stderr)
        return 2
    try:
        if not real_test_enabled():
            raise WhatsAppConfigurationError("Enable the controlled real-test settings in this terminal first.")
        test_key = os.getenv("WHATSAPP_AUTOMATION_PURCHASE_KEY", "").strip()
        if not test_key:
            raise WhatsAppConfigurationError("WHATSAPP_AUTOMATION_PURCHASE_KEY is required.")
        config = WhatsAppTestConfig.from_env()
    except WhatsAppConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    body = {
        "idempotency_key": test_key,
        "delivery_mode": "real_test",
        "customer": {"name": "LocalGrowth Test Owner", "phone": config.recipient},
        "permissions": {"thank_you": True},
        "purchase": {"items": [{"name": "Controlled template test", "category": "Test"}]},
    }
    client = api_client or httpx.Client(follow_redirects=False)
    try:
        response = client.post("http://127.0.0.1:8000/api/purchases", json=body, timeout=15)
        if response.status_code != 201:
            if response.status_code == 409:
                try:
                    message = response.json().get("error", {}).get("message", "")
                    for reason in _SAFE_REJECTIONS:
                        if message == f"Real-test request rejected: {reason}.":
                            print(f"Local backend rejected the real test: {reason}.", file=sys.stderr)
                            return 1
                except (ValueError, AttributeError, TypeError):
                    pass
            print("Local backend rejected the test purchase; check configuration and the purchase key.", file=sys.stderr)
            return 1
        job = response.json()["data"]["thank_you_job"]
        if not job or job.get("delivery_mode") != "real_test":
            print("The backend did not select real-test mode. Restart it with the same settings and purchase key.", file=sys.stderr)
            return 1
        print(f"Test job: {job['job_id']}; status: {job['status']}; scheduled_at: {job['scheduled_at']}")
        print("Reuse the same purchase key to avoid duplicate purchases. Acceptance is not confirmed delivery.")
        return 0
    except (httpx.RequestError, ValueError, KeyError, TypeError):
        print("Unable to confirm the local test purchase. Retry only with the same purchase key.", file=sys.stderr)
        return 1
    finally:
        if api_client is None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
