"""Make a consistent SQLite backup before a local schema migration."""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db import engine  # noqa: E402


def main() -> int:
    if engine.dialect.name != "sqlite" or not engine.url.database:
        print("Backup requires a configured SQLite database.", file=sys.stderr)
        return 2
    source_path = Path(engine.url.database).resolve()
    if not source_path.is_file():
        print("SQLite database does not exist; no backup was made.", file=sys.stderr)
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = source_path.with_name(f"{source_path.name}-backup-{stamp}")
    if backup_path.exists():
        print("A backup with this timestamp already exists; try again shortly.", file=sys.stderr)
        return 2
    try:
        with sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(str(backup_path)) as backup:
                source.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("SQLite backup integrity check failed.")
    except (sqlite3.Error, RuntimeError):
        print("SQLite backup failed; do not run the migration.", file=sys.stderr)
        return 1
    print(f"SQLite backup verified: {backup_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
