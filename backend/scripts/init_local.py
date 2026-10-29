"""Create persistent local-only encryption and lookup secrets if missing."""

import base64
import os
import secrets
from pathlib import Path


env_path = Path(__file__).resolve().parents[1] / ".env"
if env_path.exists():
    raise SystemExit("backend/.env already exists; it was left unchanged.")

lines = [
    "PHONE_ENCRYPTION_KEY=" + base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"),
    "PHONE_LOOKUP_SECRET=" + secrets.token_hex(32),
    "DATABASE_URL=sqlite:///./localgrowth.db",
    "THANK_YOU_DELAY_SECONDS=420",
    "PROMOTIONAL_SUPPRESSION_HOURS=168",
    "JOB_WORKER_INTERVAL_SECONDS=1",
]
env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("Created backend/.env with persistent local secrets. Keep it private and do not regenerate it after saving customer data.")
