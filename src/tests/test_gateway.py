# Gateway tests: Docker and the endpoint are replaced with fakes, so these run anywhere
import base64
import pytest

import src.gateway.gateway_app as gw
from src.watchdog.sign import sign_payload
from src.watchdog.verifier import NonceStore

SECRET = b"test-secret"


class FakeResponse:
    def raise_for_status(self):
        pass
    def json(self):
        return {"executed": True, "stdout": "ok\n", "returncode": 0}


@pytest.fixture
def client(monkeypatch):
    # Fresh secret and nonce store for every test, and record what the gateway does
    monkeypatch.setattr(gw, "SHARED_SECRET", SECRET)
    monkeypatch.setattr(gw, "nonce_store", NonceStore())
    calls = {"forwarded": [], "notified": [], "dry_run": "OK"}

    def fake_dry_run(payload):
        status = calls["dry_run"]
        return {"status": status, "stdout": "ok"} if status == "OK" else {"status": status, "detail": "fake"}

    def fake_post(url, data=None, **kwargs):
        calls["forwarded"].append(data)
        return FakeResponse()

    monkeypatch.setattr(gw, "dry_run", fake_dry_run)
    monkeypatch.setattr(gw.requests, "post", fake_post)
    monkeypatch.setattr(gw, "notify_failure", lambda stage, reason, nonce: calls["notified"].append(stage))
    return gw.app.test_client(), calls


def signed_body(payload=b"print('hi')"):
    m = sign_payload(payload, SECRET)
    return {"payload_b64": base64.b64encode(m["payload"]).decode(), "nonce": m["nonce"],
            "timestamp": m["timestamp"], "tag": m["tag"]}


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
    def broken_dry_run(payload):
        raise FileNotFoundError("docker not found")
    monkeypatch.setattr(gw, "dry_run", broken_dry_run)
    resp = c.post("/submit", json=signed_body())
    assert resp.status_code == 503
    assert calls["forwarded"] == []