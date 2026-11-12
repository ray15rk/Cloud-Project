"""Gateway contract: verified dry runs queue on EKS and never run locally."""
import base64

import pytest

import src.gateway.gateway_app as gw
from src.gateway.activity import ActivityStore
from src.gateway.cloud_submissions import CloudSubmissionStore
from src.gateway.cloud_worker import CloudWorker
from src.watchdog.sign import sign_payload
from src.watchdog.verifier import NonceStore


SECRET = b"test-secret"


def signed_body(payload=b"print('hi')", language="python"):
    message = sign_payload(payload, SECRET, language=language)
    return {"payload_b64": base64.b64encode(message["payload"]).decode(),
            "nonce": message["nonce"], "timestamp": message["timestamp"],
            "tag": message["tag"], "language": language}


@pytest.fixture
def gateway(monkeypatch, tmp_path):
    monkeypatch.setattr(gw, "SHARED_SECRET", SECRET)
    monkeypatch.setattr(gw, "EKS_ENABLED", True)
    monkeypatch.setattr(gw, "nonce_store", NonceStore())
    monkeypatch.setattr(gw, "activity_store", ActivityStore(tmp_path / "activity.sqlite3"))
    monkeypatch.setattr(gw, "cloud_store", CloudSubmissionStore(tmp_path / "cloud.sqlite3"))
    notifications = []
    forwards = []
    results = {"status": "OK"}
    monkeypatch.setattr(gw, "notify_failure", lambda stage, reason, nonce: notifications.append(stage))

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"executed": True, "returncode": 0}

    def post(url, data=None, **kwargs):
        forwards.append((data, kwargs.get("headers", {})))
        return Response()

    class Runner:
        def run(self, payload, submission_id, language):
            return {"status": results["status"], "detail": "controlled result", "job_name": "job-test"}

    monkeypatch.setattr(gw.requests, "post", post)
    monkeypatch.setattr(gw, "configured_runner", lambda: Runner())
    worker = CloudWorker(gw.cloud_store, gw.activity_store, gw.configured_runner,
                         gw.notify_failure, gw.ENDPOINT_URL)
    monkeypatch.setattr(worker, "start", lambda: None)
    monkeypatch.setattr(gw, "cloud_worker", worker)
    return gw.app.test_client(), notifications, forwards, results, worker


def finish(client, worker, status_url):
    assert client.get(status_url).status_code == 202
    row = gw.cloud_store.claim("test-worker")
    assert row is not None
    worker._process(row, "test-worker")
    response = client.get(status_url)
    assert response.status_code == 200
    return response.get_json()


def test_valid_payload_queues_eks_then_forwards(gateway):
    client, notifications, forwards, _, worker = gateway
    response = client.post("/submit", json=signed_body())
    assert response.status_code == 202
    assert response.get_json()["execution_location"] == "eks"
    assert forwards == []
    final = finish(client, worker, response.get_json()["status_url"])
    assert final["status_code"] == 200
    assert final["response"]["execution_location"] == "eks"
    assert forwards[0][0] == b"print('hi')"
    assert notifications == []


def test_disabled_eks_fails_closed_without_forwarding(gateway, monkeypatch):
    client, notifications, forwards, _, _ = gateway
    monkeypatch.setattr(gw, "EKS_ENABLED", False)
    response = client.post("/submit", json=signed_body())
    assert response.status_code == 503
    assert response.get_json()["stage"] == "dry_run"
    assert forwards == [] and notifications == ["dry_run"]
    assert gw.cloud_store.claim("test-worker") is None


def test_tampering_and_replay_never_create_extra_jobs(gateway):
    client, notifications, forwards, _, worker = gateway
    tampered = signed_body()
    tampered["payload_b64"] = base64.b64encode(b"print('evil')").decode()
    assert client.post("/submit", json=tampered).status_code == 401
    body = signed_body()
    accepted = client.post("/submit", json=body)
    assert accepted.status_code == 202
    assert client.post("/submit", json=body).status_code == 401
    assert forwards == []
    finish(client, worker, accepted.get_json()["status_url"])
    assert len(forwards) == 1
    assert notifications.count("verify") == 2


@pytest.mark.parametrize("status", ["oom_killed", "timeout", "threat_detected"])
def test_failed_eks_job_never_forwards(gateway, status):
    client, notifications, forwards, results, worker = gateway
    results["status"] = status
    accepted = client.post("/submit", json=signed_body())
    final = finish(client, worker, accepted.get_json()["status_url"])
    assert final["status_code"] == 422
    assert final["response"]["stage"] == "dry_run"
    assert forwards == [] and "dry_run" in notifications


def test_endpoint_failure_does_not_report_delivery(gateway, monkeypatch):
    client, notifications, _, _, worker = gateway
    def fail(*args, **kwargs):
        raise gw.requests.ConnectionError("refused")
    monkeypatch.setattr(gw.requests, "post", fail)
    accepted = client.post("/submit", json=signed_body())
    final = finish(client, worker, accepted.get_json()["status_url"])
    assert final["status_code"] == 503
    assert final["response"]["stage"] == "forward"
    assert "forward" in notifications


def test_language_is_preserved_for_eks_and_endpoint(gateway, monkeypatch):
    client, _, forwards, _, worker = gateway
    seen = []

    class Runner:
        def run(self, payload, submission_id, language):
            seen.append((payload, language))
            return {"status": "OK", "job_name": "job-bash"}

    worker.runner_factory = lambda: Runner()
    accepted = client.post("/submit", json=signed_body(b"echo hi", "bash"))
    finish(client, worker, accepted.get_json()["status_url"])
    assert seen == [(b"echo hi", "bash")]
    assert forwards[0][1]["X-Payload-Language"] == "bash"


def test_invalid_messages_are_rejected_before_queueing(gateway):
    client, _, _, _, _ = gateway
    assert client.post("/submit", json={"nonce": "abc"}).status_code == 400
    body = signed_body(b"puts 1", "ruby")
    assert client.post("/submit", json=body).status_code == 400
    assert gw.cloud_store.claim("test-worker") is None


def test_startup_warning_for_disabled_eks(monkeypatch):
    monkeypatch.setattr(gw, "EKS_ENABLED", False)
    monkeypatch.setattr(gw, "FALCO_ENABLED", True)
    assert "dry-run submissions are unavailable" in " ".join(gw.startup_warnings())
    monkeypatch.setattr(gw, "EKS_ENABLED", True)
    assert gw.startup_warnings() == []
