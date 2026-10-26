import os
import sqlite3
import time
import uuid

import pytest

from src.gateway.cloud_submissions import CloudCapacityError, CloudSubmissionStore


def test_queue_limit_and_restart_claim(tmp_path):
    path = tmp_path / "cloud.sqlite3"
    store = CloudSubmissionStore(path, max_inflight=1)
    first = uuid.uuid4().hex
    store.enqueue(first, "nonce-1", b"print(1)", "one.py", "test", 1.0)
    if os.name != "nt":
        assert os.stat(path).st_mode & 0o077 == 0
    with pytest.raises(CloudCapacityError):
        store.enqueue(uuid.uuid4().hex, "nonce-2", b"print(2)", "two.py", "test", 1.0)

    recovered = CloudSubmissionStore(path, max_inflight=1)
    row = recovered.claim("worker-1")
    assert row["id"] == first
    assert row["payload"] == b"print(1)"
    assert recovered.renew(first, "worker-1")
    assert recovered.begin_forward(first, "worker-1")
    assert recovered.finish(first, "worker-1", {"delivered": True}, 200)
    assert recovered.get(first)["response"]["delivered"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT payload FROM cloud_submissions WHERE id = ?", (first,)).fetchone()[0] is None
    recovered.enqueue(uuid.uuid4().hex, "nonce-2", b"print(2)", "two.py", "test", 1.0)


def test_stale_forwarding_is_ambiguous_and_never_reclaimed(tmp_path):
    store = CloudSubmissionStore(tmp_path / "cloud.sqlite3")
    submission_id = uuid.uuid4().hex
    store.enqueue(submission_id, "nonce", b"print(1)", "one.py", "test", 1.0)
    store.claim("worker-1")
    store.begin_forward(submission_id, "worker-1")
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE cloud_submissions SET updated_at = ? WHERE id = ?",
                   (time.time() - 61, submission_id))
    stale = store.mark_stale_forwarding()
    assert [row["id"] for row in stale] == [submission_id]
    assert store.get(submission_id)["state"] == "ambiguous"
    assert store.claim("worker-2") is None
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT payload FROM cloud_submissions WHERE id = ?", (submission_id,)).fetchone()[0] is None


def test_expired_running_lease_can_be_recovered(tmp_path):
    store = CloudSubmissionStore(tmp_path / "cloud.sqlite3")
    submission_id = uuid.uuid4().hex
    store.enqueue(submission_id, "nonce", b"print(1)", "one.py", "test", 1.0)
    store.claim("old-worker")
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE cloud_submissions SET lease_until = ? WHERE id = ?",
                   (time.time() - 1, submission_id))
    recovered = CloudSubmissionStore(store.path)
    assert recovered.claim("new-worker")["id"] == submission_id
    assert not recovered.begin_forward(submission_id, "old-worker")
    assert recovered.begin_forward(submission_id, "new-worker")
