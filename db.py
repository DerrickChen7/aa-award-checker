import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "app.db")
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text())
        # Idempotent migration: add carrier_filter to pre-existing routes tables.
        try:
            conn.execute(
                "ALTER TABLE routes ADD COLUMN carrier_filter "
                "TEXT NOT NULL DEFAULT 'ac_only'"
            )
        except sqlite3.OperationalError:
            pass  # column already exists


if __name__ == "__main__":
    init_db()
    print(f"Initialized {DB_PATH}")
