"""Additive, repeatable SQLite migration for controlled WhatsApp test jobs.

Run from backend/: python -m app.migrate
Existing rows, ciphertext, keys, and simulation channels are preserved.
"""

from sqlalchemy import inspect

from .db import Base, engine
from . import models  # noqa: F401 - register all tables


JOB_COLUMNS = {
    "delivery_mode": "VARCHAR(20) NOT NULL DEFAULT 'simulation'",
    "state_version": "INTEGER NOT NULL DEFAULT 0",
    "processing_started_at": "DATETIME",
    "provider_message_id": "VARCHAR(512)",
    "accepted_at": "DATETIME",
    "delivered_at": "DATETIME",
    "delivery_status": "VARCHAR(20)",
    "delivery_event_at": "DATETIME",
    "delivery_failure_code": "VARCHAR(32)",
}


def migrate_database(target_engine=engine) -> None:
    if target_engine.dialect.name != "sqlite":
        raise RuntimeError("This migration supports SQLite only.")
    with target_engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            Base.metadata.create_all(bind=connection)
            existing = {column["name"] for column in inspect(connection).get_columns("message_jobs")}
            for name, declaration in JOB_COLUMNS.items():
                if name not in existing:
                    connection.exec_driver_sql(f"ALTER TABLE message_jobs ADD COLUMN {name} {declaration}")
            connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_message_jobs_provider_message_id ON message_jobs (provider_message_id)")
            # A legacy simulation-only worker does not know to increment state_version.
            # Reject its stale updates to real-test jobs, including after acceptance.
            connection.exec_driver_sql("""
                CREATE TRIGGER IF NOT EXISTS guard_real_test_job_transitions_v1
                BEFORE UPDATE OF status, delivery_mode ON message_jobs
                WHEN NEW.delivery_mode != OLD.delivery_mode OR (
                    OLD.delivery_mode = 'real_test' AND NEW.status != OLD.status AND (
                        NEW.state_version != OLD.state_version + 1 OR
                        NOT (
                            (OLD.status = 'queued' AND NEW.status IN ('processing', 'skipped')) OR
                            (OLD.status = 'processing' AND NEW.status IN ('accepted', 'failed', 'skipped')) OR
                            (OLD.status = 'accepted' AND NEW.status IN ('delivered', 'failed'))
                        )
                    )
                )
                BEGIN
                    SELECT RAISE(ABORT, 'Invalid controlled-test job transition');
                END
            """)
            connection.commit()
        except Exception:
            connection.rollback()
            raise


if __name__ == "__main__":
    try:
        migrate_database()
    except Exception:
        raise SystemExit("Migration failed. Database contents were not printed; inspect local database access and schema.") from None
    print("WhatsApp test-job schema is ready. Existing data was preserved.")
