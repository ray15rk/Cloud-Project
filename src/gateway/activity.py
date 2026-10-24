"""Persistent activity journal. Scripts, HMAC tags, and keys are never stored."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path


class ActivityStore:
    def __init__(self, path):
        self.path = Path(path)

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS activity (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, scenario TEXT NOT NULL,
            created_at REAL NOT NULL, status_code INTEGER NOT NULL,
            delivered INTEGER NOT NULL, stage TEXT NOT NULL,
            duration_ms REAL NOT NULL, response_json TEXT NOT NULL
        )""")
        return db

    def record(self, event):
        response = dict(event["response"])
        if isinstance(response.get("endpoint_response"), dict):
            response["endpoint_response"] = dict(response["endpoint_response"])
            for key in ("stdout", "stderr"):
                value = response["endpoint_response"].get(key)
                if isinstance(value, str) and len(value) > 16000:
                    response["endpoint_response"][key] = value[:16000] + "\n[output truncated]"
        event = {**event, "response": response}
        with closing(self._connect()) as db, db:
            db.execute("""INSERT INTO activity VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                status_code=excluded.status_code, delivered=excluded.delivered,
                stage=excluded.stage, duration_ms=excluded.duration_ms,
                response_json=excluded.response_json""", (
                event["id"], event["name"], event["scenario"], event["created_at"],
                event["status_code"], int(response.get("delivered", False)),
                response.get("stage", "delivered"), event["duration_ms"], json.dumps(response),
            ))
            db.execute("""DELETE FROM activity WHERE id NOT IN (
                SELECT id FROM activity ORDER BY created_at DESC LIMIT 2000
            )""")
        return event

    def snapshot(self):
        with closing(self._connect()) as db, db:
            stats = dict(db.execute("""SELECT COUNT(*) AS total,
                COALESCE(SUM(delivered), 0) AS delivered,
                COALESCE(SUM(CASE WHEN status_code = 202 THEN 1 ELSE 0 END), 0) AS pending,
                COALESCE(AVG(duration_ms), 0) AS average_ms FROM activity""").fetchone())
            rows = db.execute("SELECT * FROM activity ORDER BY created_at DESC LIMIT 100").fetchall()
        stats["blocked"] = stats["total"] - stats["delivered"] - stats["pending"]
        stats["average_ms"] = round(stats["average_ms"], 2)
        events = [{"id": row["id"], "name": row["name"], "scenario": row["scenario"],
                   "created_at": row["created_at"], "status_code": row["status_code"],
                   "duration_ms": row["duration_ms"], "response": json.loads(row["response_json"])}
                  for row in rows]
        return {"stats": stats, "events": events, "retention": 2000}
