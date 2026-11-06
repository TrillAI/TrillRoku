"""SQLite-backed, short-lived scan manifests shared by Android and Roku clients."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

SCAN_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
SCAN_CODE_LENGTH = 10


class ScanStore:
    """Keep scanned manifests available to a paired Roku for a limited time."""

    def __init__(self, database_path: str | None = None, ttl_seconds: int = 24 * 60 * 60) -> None:
        self.database_path = database_path or os.getenv("TRILLROKU_DB_PATH", "trillroku.sqlite3")
        self.ttl_seconds = max(60, int(ttl_seconds))
        if self.database_path != ":memory:":
            Path(self.database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scans (
                scan_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                manifest_json TEXT NOT NULL
            )
            """
        )
        return connection

    def save(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """Persist a manifest and return it with a human-typeable scan code."""
        now = time.time()
        expires_at = now + self.ttl_seconds
        connection = self._connect()
        try:
            with connection:
                connection.execute("DELETE FROM scans WHERE expires_at <= ?", (now,))
                for _attempt in range(5):
                    scan_id = "".join(secrets.choice(SCAN_CODE_ALPHABET) for _ in range(SCAN_CODE_LENGTH))
                    stored = dict(manifest)
                    stored["scanId"] = scan_id
                    stored["expiresAt"] = int(expires_at)
                    try:
                        connection.execute(
                            "INSERT INTO scans(scan_id, created_at, expires_at, manifest_json) VALUES (?, ?, ?, ?)",
                            (scan_id, now, expires_at, json.dumps(stored, ensure_ascii=False)),
                        )
                        return stored
                    except sqlite3.IntegrityError:
                        continue
        finally:
            connection.close()
        raise RuntimeError("Could not allocate a unique Roku scan code.")

    def get(self, scan_id: str) -> dict[str, Any] | None:
        code = str(scan_id).strip().upper()
        if len(code) != SCAN_CODE_LENGTH or any(char not in SCAN_CODE_ALPHABET for char in code):
            return None
        now = time.time()
        connection = self._connect()
        try:
            with connection:
                connection.execute("DELETE FROM scans WHERE expires_at <= ?", (now,))
                row = connection.execute(
                    "SELECT manifest_json FROM scans WHERE scan_id = ? AND expires_at > ?",
                    (code, now),
                ).fetchone()
            if row is None:
                return None
            payload = json.loads(row[0])
            return payload if isinstance(payload, dict) else None
        finally:
            connection.close()
