"""Durable, bounded queue for EKS overflow submissions."""

import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path


class CloudCapacityError(Exception):
    pass


class CloudSubmissionStore:
    def __init__(self, path, max_inflight=3):
        self.path = Path(path)
        self.max_inflight = max_inflight

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        os.chmod(self.path, 0o600)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        db.execute("""CREATE TABLE IF NOT EXISTS cloud_submissions (
            id TEXT PRIMARY KEY, nonce TEXT NOT NULL UNIQUE,
            payload BLOB, name TEXT NOT NULL, scenario TEXT NOT NULL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            state TEXT NOT NULL, worker_id TEXT, lease_until REAL,
            verify_ms REAL NOT NULL, status_code INTEGER,
            response_json TEXT
        )""")
        return db

    def enqueue(self, submission_id, nonce, payload, name, scenario, verify_ms):
        now = time.time()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM cloud_submissions WHERE state IN ('finished', 'ambiguous') AND updated_at < ?", (now - 86400,))
            active = db.execute("""SELECT COUNT(*) FROM cloud_submissions
                WHERE state IN ('queued', 'running', 'forwarding')""").fetchone()[0]
            if active >= self.max_inflight:
                db.rollback()
                raise CloudCapacityError("EKS submission limit reached")
            db.execute("""INSERT INTO cloud_submissions
                (id, nonce, payload, name, scenario, created_at, updated_at, state, verify_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?)""",
                (submission_id, nonce, payload, name, scenario, now, now, verify_ms))
            db.commit()

    def claim(self, worker_id):
        now = time.time()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT * FROM cloud_submissions
                WHERE state = 'queued' OR (state = 'running' AND lease_until < ?)
                ORDER BY created_at LIMIT 1""", (now,)).fetchone()
            if row is None:
                db.commit()
                return None
            db.execute("""UPDATE cloud_submissions SET state = 'running', worker_id = ?,
                lease_until = ?, updated_at = ? WHERE id = ?""",
                (worker_id, now + 30, now, row["id"]))
            db.commit()
            return dict(row)

    def renew(self, submission_id, worker_id):
        with closing(self._connect()) as db, db:
            cursor = db.execute("""UPDATE cloud_submissions SET lease_until = ?, updated_at = ?
                WHERE id = ? AND worker_id = ? AND state = 'running'""",
                (time.time() + 30, time.time(), submission_id, worker_id))
            return cursor.rowcount == 1

    def begin_forward(self, submission_id, worker_id):
        with closing(self._connect()) as db, db:
            cursor = db.execute("""UPDATE cloud_submissions SET state = 'forwarding',
                updated_at = ?, lease_until = NULL WHERE id = ? AND worker_id = ?
                AND state = 'running'""", (time.time(), submission_id, worker_id))
            return cursor.rowcount == 1

    def finish(self, submission_id, worker_id, response, status_code, ambiguous=False):
        now = time.time()
        with closing(self._connect()) as db, db:
            cursor = db.execute("""UPDATE cloud_submissions SET state = ?, payload = NULL,
                response_json = ?, status_code = ?, updated_at = ?, lease_until = NULL
                WHERE id = ? AND worker_id = ? AND state IN ('running', 'forwarding')""",
                ("ambiguous" if ambiguous else "finished", json.dumps(response),
                 status_code, now, submission_id, worker_id))
            return cursor.rowcount == 1

    def mark_stale_forwarding(self):
        now = time.time()
        response = {"delivered": False, "stage": "forward",
                    "reason": "forwarding outcome is uncertain after gateway restart",
                    "execution_location": "eks"}
        with closing(self._connect()) as db, db:
            rows = db.execute("""SELECT id, name, scenario, created_at FROM cloud_submissions
                WHERE state = 'forwarding' AND updated_at < ?""", (now - 60,)).fetchall()
            db.execute("""UPDATE cloud_submissions SET state = 'ambiguous', payload = NULL,
                response_json = ?, status_code = 503, updated_at = ?
                WHERE state = 'forwarding' AND updated_at < ?""",
                (json.dumps(response), now, now - 60))
            return [dict(row) for row in rows]

    def get(self, submission_id):
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM cloud_submissions WHERE id = ?", (submission_id,)).fetchone()
            if row is None:
                return None
            return {"id": row["id"], "name": row["name"], "scenario": row["scenario"],
                    "created_at": row["created_at"], "updated_at": row["updated_at"],
                    "state": row["state"], "status_code": row["status_code"],
                    "response": json.loads(row["response_json"]) if row["response_json"] else None}
