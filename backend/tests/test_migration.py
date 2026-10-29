from sqlalchemy import create_engine, inspect, text

from app.db import Base
from app.migrate import migrate_database
from app.models import Customer, Purchase, MessageJob, WhatsAppDeliveryAttempt


def test_additive_migration_preserves_old_jobs_and_customer_ciphertext(tmp_path):
    legacy = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    try:
        tables = [table for table in Base.metadata.sorted_tables
                  if table.name not in {MessageJob.__tablename__, WhatsAppDeliveryAttempt.__tablename__}]
        Base.metadata.create_all(legacy, tables=tables)
        with legacy.begin() as connection:
            connection.execute(Customer.__table__.insert().values(
                id="cus_demo", name="Fictional migration customer", phone_ciphertext="opaque-existing-ciphertext",
                phone_lookup_hash="existing-hash", phone_last4="3210",
            ))
            connection.execute(Purchase.__table__.insert().values(id="pur_demo", customer_id="cus_demo"))
            connection.exec_driver_sql("""
                CREATE TABLE message_jobs (
                    id VARCHAR(48) PRIMARY KEY, job_type VARCHAR(24) NOT NULL,
                    channel VARCHAR(32) NOT NULL, status VARCHAR(20) NOT NULL,
                    customer_id VARCHAR(48) NOT NULL, purchase_id VARCHAR(48), campaign_id VARCHAR(48),
                    message_body TEXT NOT NULL, reason VARCHAR(80), scheduled_at DATETIME,
                    created_at DATETIME NOT NULL, completed_at DATETIME
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO message_jobs VALUES (
                    'job_demo', 'thank_you', 'whatsapp_simulated', 'queued', 'cus_demo', 'pur_demo', NULL,
                    'existing message', NULL, '2026-10-30 12:07:00', '2026-10-30 12:00:00', NULL
                )
            """)
        migrate_database(legacy)
        migrate_database(legacy)
        with legacy.connect() as connection:
            row = connection.execute(text("SELECT * FROM message_jobs")).mappings().one()
            assert row["id"] == "job_demo" and row["status"] == "queued"
            assert row["message_body"] == "existing message"
            assert row["delivery_mode"] == "simulation" and row["provider_message_id"] is None
            assert row["state_version"] == 0
            assert connection.execute(text("SELECT phone_ciphertext FROM customers")).scalar_one() == "opaque-existing-ciphertext"
            assert "whatsapp_delivery_attempts" in inspect(connection).get_table_names()
            assert connection.execute(text("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name='guard_real_test_job_transitions_v1'")).scalar_one() == 1
    finally:
        legacy.dispose()


def test_migration_does_not_requeue_existing_real_test_simulations(tmp_path):
    legacy = create_engine(f"sqlite:///{(tmp_path / 'legacy_real.db').as_posix()}")
    try:
        tables = [table for table in Base.metadata.sorted_tables
                  if table.name not in {MessageJob.__tablename__, WhatsAppDeliveryAttempt.__tablename__}]
        Base.metadata.create_all(legacy, tables=tables)
        with legacy.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE message_jobs (
                    id VARCHAR(48) PRIMARY KEY, job_type VARCHAR(24) NOT NULL,
                    channel VARCHAR(32) NOT NULL, status VARCHAR(20) NOT NULL,
                    customer_id VARCHAR(48) NOT NULL, purchase_id VARCHAR(48), campaign_id VARCHAR(48),
                    message_body TEXT NOT NULL, reason VARCHAR(80), scheduled_at DATETIME,
                    created_at DATETIME NOT NULL, completed_at DATETIME,
                    delivery_mode VARCHAR(20) NOT NULL DEFAULT 'simulation',
                    processing_started_at DATETIME, provider_message_id VARCHAR(512),
                    accepted_at DATETIME, delivered_at DATETIME
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO message_jobs VALUES (
                    'job_demo', 'thank_you', 'whatsapp_real_test', 'simulated', 'cus_demo', 'pur_demo', NULL,
                    'controlled test', NULL, '2026-10-30 12:07:00', '2026-10-30 12:00:00',
                    '2026-10-30 12:07:00', 'real_test', NULL, NULL, NULL, NULL
                )
            """)
        migrate_database(legacy)
        with legacy.connect() as connection:
            row = connection.execute(text("SELECT * FROM message_jobs")).mappings().one()
            assert row["status"] == "simulated"
            assert row["delivery_mode"] == "real_test"
            assert row["state_version"] == 0
            assert row["provider_message_id"] is None
            assert connection.execute(text("SELECT count(*) FROM whatsapp_delivery_attempts")).scalar_one() == 0
    finally:
        legacy.dispose()


def test_webhook_migration_preserves_accepted_provider_message(tmp_path):
    legacy = create_engine(f"sqlite:///{(tmp_path / 'accepted.db').as_posix()}")
    try:
        with legacy.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE message_jobs (
                    id VARCHAR(48) PRIMARY KEY, job_type VARCHAR(24) NOT NULL,
                    channel VARCHAR(32) NOT NULL, status VARCHAR(20) NOT NULL,
                    customer_id VARCHAR(48) NOT NULL, purchase_id VARCHAR(48), campaign_id VARCHAR(48),
                    message_body TEXT NOT NULL, reason VARCHAR(80), scheduled_at DATETIME,
                    created_at DATETIME NOT NULL, completed_at DATETIME,
                    delivery_mode VARCHAR(20) NOT NULL DEFAULT 'simulation',
                    state_version INTEGER NOT NULL DEFAULT 0, processing_started_at DATETIME,
                    provider_message_id VARCHAR(512), accepted_at DATETIME, delivered_at DATETIME
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO message_jobs (
                    id, job_type, channel, status, customer_id, message_body,
                    created_at, delivery_mode, provider_message_id, accepted_at
                ) VALUES (
                    'job_accepted', 'thank_you', 'whatsapp_real_test', 'accepted',
                    'cus_existing', 'test template', '2026-10-29 21:00:00',
                    'real_test', 'wamid.existing', '2026-10-29 21:01:00'
                )
            """)
        migrate_database(legacy)
        migrate_database(legacy)
        with legacy.connect() as connection:
            row = connection.execute(text("SELECT * FROM message_jobs WHERE id='job_accepted'")).mappings().one()
            assert row["status"] == "accepted" and row["provider_message_id"] == "wamid.existing"
            assert row["delivery_mode"] == "real_test" and row["state_version"] == 0
            assert row["delivery_status"] is None and row["delivery_event_at"] is None
            assert row["delivery_failure_code"] is None
            assert "ix_message_jobs_provider_message_id" in {index["name"] for index in inspect(connection).get_indexes("message_jobs")}
    finally:
        legacy.dispose()
