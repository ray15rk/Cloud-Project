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
    monkeypatch.setattr(gw, "CLOUD_FIRST", False)
    monkeypatch.setattr(gw, "nonce_store", NonceStore())
    monkeypatch.setattr(gw, "activity_store", ActivityStore(tmp_path / "activity.sqlite3"))
    monkeypatch.setattr(gw, "cloud_store", CloudSubmissionStore(tmp_path / "cloud.sqlite3"))

    # Fresh resource manager with generous budget
    # Tests check HMAC/repay/forwarding logic instead of capacity behaviour, so that need enough room that admission always succeeds 
    # Only dont successed if test deliberatyl serts right budget itself
    monkeypatch.setattr(gw, "resource_manager", ResourceManager(total_memory_mb=10_000)) 
    calls = {"forwarded": [], "forward_headers": [], "notified": [], "dry_run": "OK"}

    def fake_dry_run(payload, memory_limit=None, **kwargs):
        status = calls["dry_run"]
        return {"status": status, "stdout": "ok"} if status == "OK" else {"status": status, "detail": "fake"}

    def fake_post(url, data=None, **kwargs):
        calls["forwarded"].append(data)
        calls["forward_headers"].append(kwargs.get("headers", {}))
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
        def run(self, payload, submission_id, language):
            cloud_payloads.append((payload, language))
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
    assert cloud_payloads == [(b"print('hi')", "python")]
    assert calls["forwarded"] == [b"print('hi')"]
    assert calls["forward_headers"][0]["X-Payload-Language"] == "python"
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
        def run(self, payload, submission_id, language):
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


def test_cloud_first_sends_everything_to_eks_even_with_local_capacity(client, monkeypatch):
    c, calls = client
    monkeypatch.setattr(gw, "resource_manager", ResourceManager(total_memory_mb=10_000))   # plenty of local room
    monkeypatch.setattr(gw, "EKS_ENABLED", True)
    monkeypatch.setattr(gw, "CLOUD_FIRST", True)
    monkeypatch.setattr(gw, "dry_run", lambda *a, **k: pytest.fail("cloud-first must not run Docker locally"))
    seen = []

    class FakeRunner:
        def run(self, payload, submission_id, language):
            seen.append(payload)
            return {"status": "OK", "job_name": "run-test"}

    monkeypatch.setattr(gw, "configured_runner", lambda: FakeRunner())
    gw.cloud_worker.runner_factory = gw.configured_runner
    resp = c.post("/submit", json=signed_body())
    assert resp.status_code == 202 and resp.get_json()["execution_location"] == "eks"
    assert gw.resource_manager.snapshot()["committed_mb"] == 0
    gw.cloud_worker._process(gw.cloud_store.claim("w"), "w")
    assert seen == [b"print('hi')"] and calls["forwarded"] == [b"print('hi')"]


def test_hybrid_mode_still_prefers_local(client, monkeypatch):
    c, calls = client
    monkeypatch.setattr(gw, "EKS_ENABLED", True)
    monkeypatch.setattr(gw, "CLOUD_FIRST", False)
    monkeypatch.setattr(gw, "configured_runner", lambda: pytest.fail("must stay local while there is room"))
    assert c.post("/submit", json=signed_body()).status_code == 200


def test_startup_warnings_flag_settings_that_silently_weaken_the_setup(monkeypatch):
    for name, value in (("EKS_ENABLED", False), ("EXECUTION_MODE", "cloud"), ("FALCO_ENABLED", True), ("CLOUD_FIRST", False)):
        monkeypatch.setattr(gw, name, value)
    notes = " ".join(gw.startup_warnings())
    assert "EXECUTION_MODE=cloud has no effect" in notes and "FALCO_ENABLED=true has no effect" in notes
    monkeypatch.setattr(gw, "EKS_ENABLED", True)
    monkeypatch.setattr(gw, "EXECUTION_MODE", "hybrid")
    assert "NOT behaviour-monitored" in " ".join(gw.startup_warnings())
    monkeypatch.setattr(gw, "EXECUTION_MODE", "cloud")
    monkeypatch.setattr(gw, "CLOUD_FIRST", True)
    assert gw.startup_warnings() == []


def test_threat_detected_is_never_forwarded_and_findings_are_reported(client, monkeypatch):
    c, calls = client
    monkeypatch.setattr(gw, "EKS_ENABLED", True)
    monkeypatch.setattr(gw, "CLOUD_FIRST", True)
    behaviour = {"engine": "falco", "observed": True, "verdict": "threat", "flooded": False,
                 "findings": [{"rule": "CP Sandbox Network Socket", "behaviour": "network",
                               "priority": "Warning", "evidence": "evt.type=socket"}]}

    class FakeRunner:
        def run(self, payload, submission_id, language):
            return {"status": "threat_detected", "job_name": "run-test", "returncode": 0, "behaviour": behaviour,
                    "detail": "behavioural threat detected by Falco: CP Sandbox Network Socket"}

    monkeypatch.setattr(gw, "configured_runner", lambda: FakeRunner())
    gw.cloud_worker.runner_factory = gw.configured_runner
    response = c.post("/submit", json=signed_body())
    assert response.status_code == 202
    gw.cloud_worker._process(gw.cloud_store.claim("w"), "w")
    final = c.get(response.get_json()["status_url"]).get_json()
    assert final["status_code"] == 422
    assert final["response"]["delivered"] is False and final["response"]["stage"] == "dry_run"
    assert final["response"]["reason"].startswith("threat_detected:")
    assert final["response"]["behaviour"]["findings"][0]["behaviour"] == "network"
    assert calls["forwarded"] == []                      # never reached the endpoint
    assert "dry_run" in calls["notified"]                # the central server was told


def test_clean_behaviour_verdict_is_reported_on_delivery(client, monkeypatch):
    c, calls = client
    monkeypatch.setattr(gw, "EKS_ENABLED", True)
    monkeypatch.setattr(gw, "CLOUD_FIRST", True)

    class FakeRunner:
        def run(self, payload, submission_id, language):
            return {"status": "OK", "job_name": "run-test",
                    "behaviour": {"engine": "falco", "observed": True, "verdict": "clean", "findings": []}}

    monkeypatch.setattr(gw, "configured_runner", lambda: FakeRunner())
    gw.cloud_worker.runner_factory = gw.configured_runner
    response = c.post("/submit", json=signed_body())
    gw.cloud_worker._process(gw.cloud_store.claim("w"), "w")
    final = c.get(response.get_json()["status_url"]).get_json()
    assert final["status_code"] == 200 and final["response"]["behaviour"]["verdict"] == "clean"
    assert calls["forwarded"] == [b"print('hi')"]


def test_bash_language_is_preserved_on_cloud_path(client, monkeypatch):
    c, calls = client
    monkeypatch.setattr(gw, "resource_manager", ResourceManager(total_memory_mb=1))
    monkeypatch.setattr(gw, "EKS_ENABLED", True)

    class FakeRunner:
        def run(self, payload, submission_id, language):
            assert language == "bash"
            return {"status": "OK", "job_name": "run-bash"}

    monkeypatch.setattr(gw, "configured_runner", lambda: FakeRunner())
    gw.cloud_worker.runner_factory = gw.configured_runner
    response = c.post("/submit", json=signed_body(b"echo hi", language="bash"))
    assert response.status_code == 202
    row = gw.cloud_store.claim("test-worker")
    assert row["language"] == "bash"
    gw.cloud_worker._process(row, "test-worker")
    result = c.get(response.get_json()["status_url"]).get_json()
    assert result["status_code"] == 200
    assert calls["forward_headers"][0]["X-Payload-Language"] == "bash"
