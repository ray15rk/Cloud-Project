# Gateway tests: Docker and the endpoint are replaced with fakes, so these run anywhere
import base64
import pytest

import src.gateway.gateway_app as gw
from src.watchdog.sign import sign_payload
from src.watchdog.verifier import NonceStore
from src.resource_manager.resource_manager import ResourceManager
from src.gateway.activity import ActivityStore
from src.gateway.cloud_submissions import CloudSubmissionStore
from src.gateway.cloud_worker import CloudWorker

SECRET = b"test-secret"

class FakeResponse:
    def raise_for_status(self):
        pass
    def json(self):
        return {"executed": True, "stdout": "ok\n", "returncode": 0}


@pytest.fixture
def client(monkeypatch, tmp_path):
    # Fresh secret and nonce store for every test, and record what the gateway does
    monkeypatch.setattr(gw, "SHARED_SECRET", SECRET)
    monkeypatch.setattr(gw, "EKS_ENABLED", False)
    monkeypatch.setattr(gw, "nonce_store", NonceStore())
    monkeypatch.setattr(gw, "activity_store", ActivityStore(tmp_path / "activity.sqlite3"))
    monkeypatch.setattr(gw, "cloud_store", CloudSubmissionStore(tmp_path / "cloud.sqlite3"))

    # Fresh resource manager with generous budget
    # Tests check HMAC/repay/forwarding logic instead of capacity behaviour, so that need enough room that admission always succeeds 
    # Only dont successed if test deliberatyl serts right budget itself
    monkeypatch.setattr(gw, "resource_manager", ResourceManager(total_memory_mb=10_000)) 
    calls = {"forwarded": [], "notified": [], "dry_run": "OK"}

    def fake_dry_run(payload, memory_limit=None, **kwargs):
        status = calls["dry_run"]
        return {"status": status, "stdout": "ok"} if status == "OK" else {"status": status, "detail": "fake"}

    def fake_post(url, data=None, **kwargs):
        calls["forwarded"].append(data)
        return FakeResponse()

    monkeypatch.setattr(gw, "dry_run", fake_dry_run)
    monkeypatch.setattr(gw.requests, "post", fake_post)
    monkeypatch.setattr(gw, "notify_failure", lambda stage, reason, nonce: calls["notified"].append(stage))
    worker = CloudWorker(gw.cloud_store, gw.activity_store, gw.configured_runner,
                         gw.notify_failure, gw.ENDPOINT_URL)
    monkeypatch.setattr(worker, "start", lambda: None)
    monkeypatch.setattr(gw, "cloud_worker", worker)
    return gw.app.test_client(), calls


def signed_body(payload=b"print('hi')", language="python"):
    m = sign_payload(payload, SECRET, language=language)
    return {"payload_b64": base64.b64encode(m["payload"]).decode(), "nonce": m["nonce"],
            "timestamp": m["timestamp"], "tag": m["tag"], "language": language}

def test_language_changed_in_transit_is_dropped(client):
    c, calls = client
    body = signed_body(b"echo hi", language="bash")
    body["language"] = "powershell" # attacker swaps interpreter, keeps old tag
    resp = c.post("/submit", json=body)
    assert resp.status_code == 401
    assert resp.get_json()["stage"] == "verify"
    assert calls["forwarded"] == []

def test_unsupported_language_is_rejected_before_verification(client):
    c, calls = client
    resp = c.post("/submit", json=signed_body(b"puts 1", language="ruby"))
    assert resp.status_code == 400
    assert resp.get_json()["stage"] == "parse"


def test_valid_payload_is_delivered(client):
    c, calls = client
    resp = c.post("/submit", json=signed_body())
    assert resp.status_code == 200
    assert resp.get_json()["delivered"] is True
    assert calls["forwarded"] == [b"print('hi')"]  # The original payload reached the endpoint
    assert calls["notified"] == []


def test_tampered_payload_is_dropped(client):
    c, calls = client
    body = signed_body()
    body["payload_b64"] = base64.b64encode(b"print('evil')").decode()
    resp = c.post("/submit", json=body)
    assert resp.status_code == 401
    assert resp.get_json()["stage"] == "verify"
    assert calls["forwarded"] == []        # Never reached the endpoint
    assert calls["notified"] == ["verify"]  # Central server was told


def test_replayed_payload_is_dropped(client):
    c, calls = client
    body = signed_body()
    assert c.post("/submit", json=body).status_code == 200
    resp = c.post("/submit", json=body)
    assert resp.status_code == 401
    assert "Replay" in resp.get_json()["reason"]
    assert len(calls["forwarded"]) == 1     # Only the first copy was delivered


@pytest.mark.parametrize("status", ["oom_killed", "timeout", "error"])
def test_unsafe_payload_is_dropped_after_dry_run(client, status):
    c, calls = client
    calls["dry_run"] = status
    resp = c.post("/submit", json=signed_body())
    assert resp.status_code == 422
    assert resp.get_json()["stage"] == "dry_run"
    assert calls["forwarded"] == []
    assert calls["notified"] == ["dry_run"]


def test_malformed_message_is_rejected(client):
    c, calls = client
    resp = c.post("/submit", json={"nonce": "abc"})  # Missing fields
    assert resp.status_code == 400
    assert calls["forwarded"] == []


def test_non_json_body_is_rejected(client):
    c, calls = client
    resp = c.post("/submit", data=b"not json at all")
    assert resp.status_code == 400


def test_endpoint_down_is_reported(client, monkeypatch):
    c, calls = client
    def failing_post(*args, **kwargs):
        raise gw.requests.ConnectionError("refused")
    monkeypatch.setattr(gw.requests, "post", failing_post)
    resp = c.post("/submit", json=signed_body())
    assert resp.status_code == 502
    assert calls["notified"] == ["forward"]


def test_sandbox_crash_fails_closed(client, monkeypatch):
    # If Docker is down, the payload must be dropped, not forwarded untested
    c, calls = client
    def broken_dry_run(payload, memory_limit=None):
        raise FileNotFoundError("docker not found")
    monkeypatch.setattr(gw, "dry_run", broken_dry_run)
    resp = c.post("/submit", json=signed_body())
    assert resp.status_code == 503
    assert resp.get_json()["stage"] == "dry_run"
    assert calls["forwarded"] == []

def test_capacity_exceeded_rejected(client, monkeypatch):
    c, calls = client
    monkeypatch.setattr(gw, "resource_manager", ResourceManager(total_memory_mb=1))
    resp = c.post("/submit", json=signed_body())

    assert resp.status_code == 503
    assert resp.get_json()["stage"] == "capacity"
    assert calls["forwarded"] == [] # Never reach endpoint
    assert calls["notified"] == ["capacity"]


def test_verified_overflow_uses_eks_and_forwards_only_after_success(client, monkeypatch):
    c, calls = client
    manager = ResourceManager(total_memory_mb=1)
    monkeypatch.setattr(gw, "resource_manager", manager)
    monkeypatch.setattr(gw, "EKS_ENABLED", True)
    cloud_payloads = []

    class FakeRunner:
        def run(self, payload, submission_id):
            cloud_payloads.append(payload)
            return {"status": "OK", "job_name": "run-test"}

    monkeypatch.setattr(gw, "configured_runner", lambda: FakeRunner())
    gw.cloud_worker.runner_factory = gw.configured_runner
    response = c.post("/submit", json=signed_body())
    assert response.status_code == 202
    assert response.get_json()["execution_location"] == "eks"
    assert calls["forwarded"] == []
    assert c.get(response.get_json()["status_url"]).status_code == 202
    row = gw.cloud_store.claim("test-worker")
    gw.cloud_worker._process(row, "test-worker")
    result = c.get(response.get_json()["status_url"]).get_json()
    assert result["status_code"] == 200
    assert result["response"]["delivered"] is True
    assert cloud_payloads == [b"print('hi')"]
    assert calls["forwarded"] == [b"print('hi')"]
    assert manager.snapshot()["committed_mb"] == 0


def test_bad_signature_never_reaches_eks(client, monkeypatch):
    c, calls = client
    monkeypatch.setattr(gw, "resource_manager", ResourceManager(total_memory_mb=1))
    monkeypatch.setattr(gw, "EKS_ENABLED", True)
    monkeypatch.setattr(gw, "configured_runner", lambda: pytest.fail("EKS must not run"))
    body = signed_body()
    body["payload_b64"] = base64.b64encode(b"print('tampered')").decode()
    assert c.post("/submit", json=body).status_code == 401
    assert calls["forwarded"] == []


def test_failed_eks_job_never_forwards(client, monkeypatch):
    c, calls = client
    monkeypatch.setattr(gw, "resource_manager", ResourceManager(total_memory_mb=1))
    monkeypatch.setattr(gw, "EKS_ENABLED", True)

    class FakeRunner:
        def run(self, payload, submission_id):
            return {"status": "oom_killed", "detail": "128Mi", "job_name": "run-failed"}

    monkeypatch.setattr(gw, "configured_runner", lambda: FakeRunner())
    gw.cloud_worker.runner_factory = gw.configured_runner
    response = c.post("/submit", json=signed_body())
    assert response.status_code == 202
    assert response.get_json()["execution_location"] == "eks"
    row = gw.cloud_store.claim("test-worker")
    gw.cloud_worker._process(row, "test-worker")
    result = c.get(response.get_json()["status_url"]).get_json()
    assert result["status_code"] == 422
    assert calls["forwarded"] == []
