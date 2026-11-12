"""Dashboard checks for the EKS-only gateway."""
import re
from types import SimpleNamespace

import pytest

import src.gateway.gateway_app as gw
import src.gateway.dashboard as dashboard
from src.gateway.activity import ActivityStore
from src.gateway.cloud_submissions import CloudSubmissionStore
from src.gateway.cloud_worker import CloudWorker
from src.watchdog.verifier import NonceStore


@pytest.fixture
def operator(monkeypatch, tmp_path):
    monkeypatch.setattr(gw, "SHARED_SECRET", b"dashboard-test-private-secret")
    monkeypatch.setattr(gw, "EKS_ENABLED", True)
    monkeypatch.setattr(gw, "nonce_store", NonceStore())
    monkeypatch.setattr(gw, "activity_store", ActivityStore(tmp_path / "activity.sqlite3"))
    monkeypatch.setattr(gw, "cloud_store", CloudSubmissionStore(tmp_path / "cloud.sqlite3"))
    forwards = []

    class Runner:
        def run(self, payload, submission_id, language):
            return {"status": "OK", "job_name": "test-job"}

    def post(*args, **kwargs):
        forwards.append(kwargs.get("data"))
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: {"executed": True, "returncode": 0})

    monkeypatch.setattr(gw.requests, "post", post)
    monkeypatch.setattr(gw, "notify_failure", lambda *args: None)
    monkeypatch.setattr(gw, "configured_runner", lambda: Runner())
    worker = CloudWorker(gw.cloud_store, gw.activity_store, gw.configured_runner,
                         gw.notify_failure, gw.ENDPOINT_URL)
    monkeypatch.setattr(worker, "start", lambda: None)
    monkeypatch.setattr(gw, "cloud_worker", worker)
    client = gw.app.test_client()
    token = re.search(r'name="dashboard-token" content="([^"]+)"', client.get("/").text).group(1)
    return client, {"X-Dashboard-Token": token}, forwards, worker


def test_page_and_assets_do_not_expose_signing_key(operator):
    client, _, _, _ = operator
    page = client.get("/")
    assert page.status_code == 200
    assert "dashboard-test-private-secret" not in page.text
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    for asset in ("dashboard.js", "dashboard.css", "mark.svg"):
        assert client.get("/dashboard-assets/" + asset).status_code == 200


def test_signing_requires_token_and_loopback(operator):
    client, headers, _, _ = operator
    payload = {"code": "print('hello')"}
    assert client.post("/api/run", json=payload).status_code == 403
    assert client.post("/api/run", json=payload, headers=headers,
                       environ_base={"REMOTE_ADDR": "192.168.1.7"}).status_code == 403
    assert client.post("/api/run", json=payload,
                       headers={**headers, "Host": "attacker.example"}).status_code == 403


def test_dashboard_queues_eks_and_records_final_result(operator):
    client, headers, forwards, worker = operator
    response = client.post("/api/run", json={"code": "print('hello')", "name": "hello.py"}, headers=headers)
    assert response.status_code == 200
    attempt = response.json["attempts"][0]
    assert attempt["status_code"] == 202
    assert attempt["response"]["execution_location"] == "eks"
    assert forwards == []
    row = gw.cloud_store.claim("test-worker")
    assert row is not None
    worker._process(row, "test-worker")
    final = client.get(attempt["response"]["status_url"]).json
    assert final["status_code"] == 200
    assert final["response"]["delivered"] is True
    assert forwards == [b"print('hello')"]
    assert client.get("/api/overview").json["stats"]["delivered"] == 1


def test_tamper_and_replay_do_not_forward_unchecked_payloads(operator):
    client, headers, forwards, worker = operator
    tamper = client.post("/api/run", json={"code": "print(1)", "mode": "tamper"}, headers=headers)
    assert tamper.json["attempts"][0]["status_code"] == 401
    replay = client.post("/api/run", json={"code": "print(1)", "mode": "replay"}, headers=headers)
    assert [entry["status_code"] for entry in replay.json["attempts"]] == [202, 401]
    assert forwards == []
    worker._process(gw.cloud_store.claim("test-worker"), "test-worker")
    assert len(forwards) == 1


@pytest.mark.parametrize("payload", [None, [], {}, {"code": " "}, {"code": 3},
                                      {"code": "print(1)", "mode": []},
                                      {"code": "print(1)", "name": {}}])
def test_invalid_form_data_is_rejected(operator, payload):
    client, headers, _, _ = operator
    assert client.post("/api/run", json=payload, headers=headers).status_code == 400


def test_missing_secret_blocks_run(operator, monkeypatch):
    client, headers, _, _ = operator
    monkeypatch.setattr(gw, "SHARED_SECRET", b"")
    assert client.post("/api/run", json={"code": "print(1)"}, headers=headers).status_code == 503


def test_manual_health_refresh_bypasses_cache(operator, monkeypatch):
    client, _, _, _ = operator
    available = [True]
    monkeypatch.setattr(dashboard.requests, "get", lambda *a, **kw: SimpleNamespace(
        ok=available[0], json=lambda: {"status": "ok"}))
    assert client.get("/api/health?refresh=1").json["endpoint"]["status"] == "online"
    available[0] = False
    assert client.get("/api/health").json["endpoint"]["status"] == "online"
    assert client.get("/api/health?refresh=1").json["endpoint"]["status"] == "unavailable"
