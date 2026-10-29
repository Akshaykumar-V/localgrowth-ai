"""Run the real backend against an isolated temporary database for smoke testing.

No production/local shop database or secrets are read or modified.
"""
import argparse
import os
from pathlib import Path
import secrets
import sys
import tempfile

from cryptography.fernet import Fernet
import uvicorn

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8011)
args = parser.parse_args()
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

with tempfile.TemporaryDirectory(prefix="localgrowth-integration-") as temporary:
    os.environ.update(
        DATABASE_URL=f"sqlite:///{Path(temporary).as_posix()}/smoke.db",
        PHONE_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        PHONE_LOOKUP_SECRET=secrets.token_hex(32),
        THANK_YOU_DELAY_SECONDS="2",
        JOB_WORKER_INTERVAL_SECONDS="0.1",
        JOB_WORKER_ENABLED="true",
        PROMOTIONAL_SUPPRESSION_HOURS="168",
    )
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, access_log=False)
