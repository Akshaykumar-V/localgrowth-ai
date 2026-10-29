import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


_db_path = Path(tempfile.gettempdir()) / f"localgrowth-test-{os.getpid()}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"
os.environ["PHONE_ENCRYPTION_KEY"] = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
os.environ["PHONE_LOOKUP_SECRET"] = "test-only-fake-secret-not-for-local-use-00000000000000000000"
os.environ["THANK_YOU_DELAY_SECONDS"] = "420"
os.environ["PROMOTIONAL_SUPPRESSION_HOURS"] = "168"
os.environ["JOB_WORKER_ENABLED"] = "false"

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(clean_database):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def purchase_request():
    def build(phone="9876543210", category="Kurtis", permissions=None, key=None, **purchase_overrides):
        body = {
            "customer": {"name": "Maya Demo", "phone": phone},
            "purchase": {
                "items": [{"name": "Indigo kurti", "category": category}],
                **purchase_overrides,
            },
        }
        if permissions is not None:
            body["permissions"] = permissions
        if key is not None:
            body["idempotency_key"] = key
        return body
    return build
