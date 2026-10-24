import re
from types import SimpleNamespace

import pytest
import src.gateway.gateway_app as gw
import src.gateway.dashboard as dashboard
from src.gateway.activity import ActivityStore
from src.resource_manager.resource_manager import ResourceManager
from src.watchdog.verifier import NonceStore


@pytest.fixture
def operator(monkeypatch, tmp_path):
    monkeypatch.setattr(gw, "SHARED_SECRET", b"dashboard-test-private-secret")
    monkeypatch.setattr(gw, "EKS_ENABLED", False)
    monkeypatch.setattr(gw, "nonce_store", NonceStore())
    monkeypatch.setattr(gw, "resource_manager", ResourceManager())
    monkeypatch.setattr(gw, "activity_store", ActivityStore(tmp_path / "activity.sqlite3"))
    monkeypatch.setattr(gw, "notify_failure", lambda *args: None)
    calls = {"dry_runs": 0, "forwards": 0, "status": "OK"}
    def dry_run(payload, memory_limit=None):
        calls["dry_runs"] += 1
        return {"status": calls["status"], "detail": "controlled test result"}
    def post(*args, **kwargs):
        calls["forwards"] += 1
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"executed": True, "returncode": 0, "stdout": "delivered"})
    monkeypatch.setattr(gw, "dry_run", dry_run)
    monkeypatch.setattr(gw.requests, "post", post)
    client = gw.app.test_client()
    page = client.get("/")
    token = re.search(r'name="dashboard-token" content="([^"]+)"', page.text).group(1)
    return client, {"X-Dashboard-Token": token}, calls


def test_page_and_assets_do_not_expose_signing_key(operator):
    client, _, _ = operator
    page = client.get("/")
    assert page.status_code == 200
    assert "Relay" in page.text
    assert "dashboard-test-private-secret" not in page.text
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    for asset in ("dashboard.js", "dashboard.css", "mark.svg"):
        assert client.get("/dashboard-assets/" + asset).status_code == 200


def test_signing_requires_page_token_and_loopback(operator):
    client, headers, calls = operator
    payload = {"code": "print('hello')"}
    assert client.post("/api/run", json=payload).status_code == 403
    assert client.post("/api/run", json=payload, headers=headers, environ_base={"REMOTE_ADDR": "192.168.1.7"}).status_code == 403
    assert client.post("/api/run", json=payload, headers={**headers, "Host": "attacker.example"}).status_code == 403
    assert calls["dry_runs"] == 0


def test_signed_submission_records_real_result_and_capacity_release(operator):
    client, headers, calls = operator
    response = client.post("/api/run", json={"code": "print('hello')", "name": "hello.py"}, headers=headers)
    assert response.status_code == 200
    assert response.json["attempts"][0]["response"]["delivered"] is True
    snapshot = client.get("/api/overview").json
    assert snapshot["stats"]["delivered"] == 1
    assert snapshot["events"][0]["name"] == "hello.py"
    assert snapshot["resources"]["committed_mb"] == 0
    assert calls["forwards"] == 1
    # A new store instance can read history; restarting the gateway does not erase it.
    assert ActivityStore(gw.activity_store.path).snapshot()["stats"]["total"] == 1


def test_tamper_is_not_executed_and_has_timing(operator):
    client, headers, calls = operator
    attempt = client.post("/api/run", json={"code": "print('hello')", "mode": "tamper"}, headers=headers).json["attempts"][0]
    assert attempt["status_code"] == 401
    assert attempt["response"]["stage"] == "verify"
    assert "verify_ms" in attempt["response"]["timings_ms"]
    assert calls["dry_runs"] == calls["forwards"] == 0


def test_replay_sends_exactly_one_payload_to_endpoint(operator):
    client, headers, calls = operator
    result = client.post("/api/run", json={"code": "print('hello')", "mode": "replay"}, headers=headers)
    assert [entry["status_code"] for entry in result.json["attempts"]] == [200, 401]
    assert "Replay" in result.json["attempts"][1]["response"]["reason"]
    assert calls["dry_runs"] == calls["forwards"] == 1


@pytest.mark.parametrize("status", ["timeout", "oom_killed", "error"])
def test_sandbox_failures_are_visible_and_never_forwarded(operator, status):
    client, headers, calls = operator
    calls["status"] = status
    attempt = client.post("/api/run", json={"code": "print('hello')"}, headers=headers).json["attempts"][0]
    assert attempt["status_code"] == 422
    assert status in attempt["response"]["reason"]
    assert calls["forwards"] == 0


@pytest.mark.parametrize("payload", [None, [], {}, {"code": " "}, {"code": 3}, {"code": "print(1)", "mode": []}, {"code": "print(1)", "name": {}}])
def test_invalid_form_data_is_rejected(operator, payload):
    client, headers, calls = operator
    assert client.post("/api/run", json=payload, headers=headers).status_code == 400
    assert calls["dry_runs"] == 0


def test_size_limit_and_missing_secret(operator, monkeypatch):
    client, headers, calls = operator
    assert client.post("/api/run", json={"code": "x" * 65537}, headers=headers).status_code == 413
    monkeypatch.setattr(gw, "SHARED_SECRET", b"")
    assert client.post("/api/run", json={"code": "print(1)"}, headers=headers).status_code == 503
    assert client.post("/api/capacity-test", json={}, headers=headers).status_code == 503
    assert calls["dry_runs"] == 0


def test_capacity_failure_is_recorded_without_execution(operator, monkeypatch):
    client, headers, calls = operator
    monkeypatch.setattr(gw, "resource_manager", ResourceManager(total_memory_mb=1))
    response = client.post("/api/capacity-test", json={}, headers=headers)
    assert response.status_code == 200
    assert all(entry["response"]["stage"] == "capacity" for entry in response.json["attempts"])
    assert calls["dry_runs"] == 0
    assert client.get("/api/overview").json["stats"]["blocked"] == 2


def test_endpoint_bad_json_is_a_failure_not_an_internal_server_error(operator, monkeypatch):
    client, headers, calls = operator
    def bad_json():
        raise ValueError("not json")
    monkeypatch.setattr(gw.requests, "post", lambda *a, **kw: SimpleNamespace(raise_for_status=lambda: None, json=bad_json))
    attempt = client.post("/api/run", json={"code": "print(1)"}, headers=headers).json["attempts"][0]
    assert attempt["status_code"] == 502
    assert attempt["response"]["stage"] == "forward"


def test_endpoint_nonzero_exit_is_not_reported_as_success(operator, monkeypatch):
    client, headers, _ = operator
    monkeypatch.setattr(gw.requests, "post", lambda *a, **kw: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"executed": True, "returncode": 1}))
    attempt = client.post("/api/run", json={"code": "print(1)"}, headers=headers).json["attempts"][0]
    assert attempt["status_code"] == 502
    assert not attempt["response"]["delivered"]


def test_examples_are_fixed_project_files(operator):
    client, _, _ = operator
    response = client.get("/api/examples")
    assert response.status_code == 200
    assert {entry["id"] for entry in response.json["examples"]} == {"good", "infinite_loop", "memory_hog"}


def test_invalid_unicode_credentials_are_rejected_without_a_server_error(operator):
    client, headers, calls = operator
    from src.watchdog.sign import sign_payload
    import base64
    signed = sign_payload(b"print(1)", gw.SHARED_SECRET)
    signed["payload_b64"] = base64.b64encode(signed.pop("payload")).decode()
    signed["tag"] = "\u00e9" * 64
    assert client.post("/submit", json=signed).status_code == 400
    response = client.post("/api/run", json={"code": "print(1)"}, headers={"X-Dashboard-Token": "\u00e9"})
    assert response.status_code == 403
    assert calls["dry_runs"] == 0


def test_invalid_utf8_script_is_a_validation_error(operator):
    client, headers, calls = operator
    assert client.post("/api/run", json={"code": "\ud800"}, headers=headers).status_code == 400
    assert calls["dry_runs"] == 0


def test_tamper_at_the_script_size_limit_still_tests_integrity(operator):
    client, headers, calls = operator
    code = "#" + "x" * 65535
    result = client.post("/api/run", json={"code": code, "mode": "tamper"}, headers=headers)
    assert result.status_code == 200
    assert result.json["attempts"][0]["status_code"] == 401
    assert result.json["attempts"][0]["response"]["stage"] == "verify"
    assert calls["dry_runs"] == 0


def test_manual_health_refresh_bypasses_the_cached_status(operator, monkeypatch):
    client, _, _ = operator
    available = [True]
    monkeypatch.setattr(dashboard.requests, "get", lambda *a, **kw: SimpleNamespace(ok=available[0], json=lambda: {"status": "ok"}))
    monkeypatch.setattr(dashboard.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout="linux\n"))
    assert client.get("/api/health?refresh=1").json["endpoint"]["status"] == "online"
    available[0] = False
    assert client.get("/api/health").json["endpoint"]["status"] == "online"
    assert client.get("/api/health?refresh=1").json["endpoint"]["status"] == "unavailable"


@pytest.mark.parametrize("health_body", [None, [], "not an object"])
def test_invalid_service_health_is_reported_as_unavailable(operator, monkeypatch, health_body):
    client, _, _ = operator
    monkeypatch.setattr(dashboard.requests, "get", lambda *a, **kw: SimpleNamespace(ok=True, json=lambda: health_body))
    monkeypatch.setattr(dashboard.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout="linux\n"))
    response = client.get("/api/health?refresh=1")
    assert response.status_code == 200
    assert response.json["endpoint"]["status"] == "unavailable"


def test_health_checks_report_actual_dependencies(operator, monkeypatch):
    client, _, _ = operator
    monkeypatch.setattr(dashboard.requests, "get", lambda *a, **kw: SimpleNamespace(ok=True, json=lambda: {"status": "ok"}))
    monkeypatch.setattr(dashboard.subprocess, "run", lambda args, **kw: SimpleNamespace(returncode=0, stdout="linux\n"))
    health = client.get("/api/health?refresh=1").json
    assert health["docker"]["status"] == "online"
    assert health["image"]["status"] == "ready"
    assert health["endpoint"]["status"] == "online"
    assert health["eks"]["status"] == "disabled"
