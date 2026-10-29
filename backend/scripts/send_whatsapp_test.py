"""Send one explicitly requested WhatsApp Cloud API test template."""

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.whatsapp import (  # noqa: E402
    WhatsAppConfigurationError,
    WhatsAppSendError,
    WhatsAppTestConfig,
    send_test_template,
)


def main(argv: list[str] | None = None, *, sender=send_test_template) -> int:
    parser = argparse.ArgumentParser(description="Send one Meta-approved WhatsApp test template to the configured allowlisted recipient.")
    parser.add_argument("--send", action="store_true", help="Actually contact Meta. Without this flag, no network request is made.")
    args = parser.parse_args(argv)

    if not args.send:
        print("Dry run only; no request was sent. Add --send to contact Meta.")
        return 0

    try:
        config = WhatsAppTestConfig.from_env()
        message_id = sender(config)
    except WhatsAppConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except WhatsAppSendError as exc:
        print(f"WhatsApp test failed: {exc}", file=sys.stderr)
        return 1

    print(f"Meta accepted the test message. Message ID: {message_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
