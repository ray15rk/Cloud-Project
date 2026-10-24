import json

import pytest
import requests

from scripts.monitor_nginx import poll
from src.sender import send
from src.ingress import GATEWAY_URL


def test_default_sender_targets_ingress():
    assert GATEWAY_URL == "http://127.0.0.1:8080/submit"


def test_sender_handles_html_error_without_dumping_page():
    response = requests.Response()
    response.status_code = 502
    response.reason = "Bad Gateway"
    response._content = b"<html>upstream unavailable</html>"
    response.headers["X-Request-ID"] = "test-id"
    assert send.response_body(response) == {
        "delivered": False, "stage": "ingress", "status": 502,
        "reason": "Bad Gateway", "request_id": "test-id"}


def test_sender_does_not_retry_unknown_execution(monkeypatch, tmp_path):
    script = tmp_path / "good.py"
    script.write_text("print('hello')")
    monkeypatch.setattr("sys.argv", ["send", str(script)])
    calls = []
    def timed_out(*args, **kwargs):
        calls.append(kwargs)
        raise requests.Timeout()
    monkeypatch.setattr(send.requests, "post", timed_out)
    with pytest.raises(SystemExit, match="outcome may be unknown"):
        send.main()
    assert len(calls) == 1


def event(status, **extra):
    return json.dumps({"status": status, "request_id": "id", "upstream_status": "-", **extra}) + "\n"


def test_monitor_aggregates_and_checkpoints_only_after_ack(monkeypatch, tmp_path):
    log, state = tmp_path / "access.log", tmp_path / "state.json"
    log.write_text(event(429) * 10 + event(413) + event(502, upstream_status="502",
                   upstream_header_time="0.001"))  # gateway already notifies its own failures
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs["json"])
        response = requests.Response()
        response.status_code = 200
        return response
    monkeypatch.setattr("scripts.monitor_nginx.requests.post", post)
    report = poll(log, state, "http://localhost/notify")
    assert report["counts"] == {"429": 10, "413": 1}
    assert len(report["sample_request_ids"]) == 3
    assert poll(log, state, "http://localhost/notify") is None
    assert len(calls) == 1


def test_monitor_retries_failed_notification(monkeypatch, tmp_path):
    log, state = tmp_path / "access.log", tmp_path / "state.json"
    log.write_text(event(502, upstream_status="502", upstream_header_time="-"))
    def down(*args, **kwargs):
        raise requests.ConnectionError()
    monkeypatch.setattr("scripts.monitor_nginx.requests.post", down)
    with pytest.raises(requests.ConnectionError):
        poll(log, state, "http://localhost/notify")
    assert not state.exists()


def test_monitor_waits_for_complete_lines_and_handles_rotation(monkeypatch, tmp_path):
    log, state = tmp_path / "access.log", tmp_path / "state.json"
    line = event(429)
    log.write_text(line[:-1])
    reports = []
    def post(url, **kwargs):
        reports.append(kwargs["json"])
        response = requests.Response()
        response.status_code = 200
        return response
    monkeypatch.setattr("scripts.monitor_nginx.requests.post", post)
    assert poll(log, state, "http://localhost/notify") is None
    log.write_text(line)
    assert poll(log, state, "http://localhost/notify")["counts"] == {"429": 1}
    log.rename(tmp_path / "old.log")
    log.write_text(event(413))
    assert poll(log, state, "http://localhost/notify")["counts"] == {"413": 1}
