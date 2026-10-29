"""Verify the local or HTTPS webhook routes without sending a WhatsApp message."""

import argparse
import hashlib
import hmac
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import config as _config  # noqa: E402,F401 - load private .env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Meta webhook verification and signature locally; no message is sent.")
    parser.add_argument("--url", default="http://127.0.0.1:8001/api/webhooks/whatsapp")
    args = parser.parse_args(argv)
    parsed = urlsplit(args.url)
    if parsed.path != "/api/webhooks/whatsapp" or parsed.query or parsed.fragment or (
        parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"})
    ):
        print("Use the webhook path on HTTPS, or HTTP loopback for a local check.", file=sys.stderr)
        return 2
    verify_token = os.getenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "")
    app_secret = os.getenv("WHATSAPP_APP_SECRET", "")
    if not verify_token or not app_secret:
        print("Webhook verify token and app secret must be configured locally.", file=sys.stderr)
        return 2
    challenge = secrets.token_urlsafe(16)
    body = b'{"object":"whatsapp_business_account","entry":[]}'
    signature = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    try:
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            verification = client.get(args.url, params={
                "hub.mode": "subscribe", "hub.verify_token": verify_token, "hub.challenge": challenge,
            })
            callback = client.post(args.url, content=body, headers={
                "Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={signature}",
            })
    except httpx.RequestError:
        print("Webhook check could not reach the listener.", file=sys.stderr)
        return 1
    if verification.status_code != 200 or verification.text != challenge or callback.status_code != 200:
        print(f"Webhook check failed (GET {verification.status_code}, POST {callback.status_code}).", file=sys.stderr)
        return 1
    print("Webhook GET challenge and signed POST passed. No message was sent or job changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
