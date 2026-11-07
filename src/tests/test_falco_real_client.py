"""FalcoMonitor through the REAL Kubernetes client, against a local fake API server.

The fakes in falco_testkit.py are faithful to the client's JSON-reinterpretation quirk, but these tests
remove all doubt: the real CoreV1Api makes real HTTP calls to a small server that serves the Falco pod
list and the Falco log as text/plain. A clean run's log holds exactly one alert, the start marker, and
once went unseen because the client returns such a log as a Python repr instead of JSON.
"""
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from kubernetes import client

from falco_testkit import CONTAINER, EXEC_CHILD, NET_SOCKET, OBSERVED, POD
from src.sandbox.falco_monitor import FalcoMonitor, MonitorError, Settings


class FakeApiServer:
    """Just enough of the Kubernetes API for the monitor: list Falco pods, read a Falco pod's log."""

    def __init__(self):
        outer = self
        self.log, self.falco_pods, self.requests = "", [("falco-abcde", "node-a", "Running")], []

        class Handler(BaseHTTPRequestHandler):
            def reply(self, content_type, body):
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                outer.requests.append(self.path)
                path = self.path.split("?")[0]
                if path == "/api/v1/namespaces/falco/pods":
                    items = [{"metadata": {"name": name, "namespace": "falco"},
                              "spec": {"nodeName": node, "containers": [{"name": "falco"}]},
                              "status": {"phase": phase}} for name, node, phase in outer.falco_pods]
                    self.reply("application/json", json.dumps({"kind": "PodList", "apiVersion": "v1",
                                                               "metadata": {}, "items": items}))
                elif path.endswith("/log"):
                    self.reply("text/plain", outer.log)            # the API serves logs as text/plain
                else:
                    self.send_error(404)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def core(self):
        configuration = client.Configuration()
        configuration.host = f"http://127.0.0.1:{self.server.server_port}"
        return client.CoreV1Api(client.ApiClient(configuration))


@pytest.fixture(scope="module")
def api():
    server = FakeApiServer()
    yield server
    server.server.shutdown()


@pytest.fixture
def sandbox_pod():
    now = datetime.now(timezone.utc)
    status = client.V1ContainerStatus(name="sandbox", image="sandbox", image_id="sandbox", ready=False,
                                      restart_count=0, container_id="containerd://" + CONTAINER)
    return client.V1Pod(metadata=client.V1ObjectMeta(name=POD, creation_timestamp=now - timedelta(seconds=5)),
                        spec=client.V1PodSpec(node_name="node-a", containers=[client.V1Container(name="sandbox")]),
                        status=client.V1PodStatus(container_statuses=[status]))


def monitor():
    return FalcoMonitor(Settings(settle_seconds=0.0, wait_seconds=0.0), sleep=lambda seconds: None)


def test_a_clean_runs_single_start_alert_is_seen(api, sandbox_pod):
    """The regression: one JSON line in the log window, which the client used to hand back as a repr."""
    api.log = OBSERVED + "\n"
    behaviour = monitor().collect(api.core(), sandbox_pod)
    assert behaviour.observed and not behaviour.threat


def test_a_threat_run_is_seen(api, sandbox_pod):
    api.log = OBSERVED + "\n" + NET_SOCKET + "\n" + EXEC_CHILD + "\n"
    behaviour = monitor().collect(api.core(), sandbox_pod)
    assert behaviour.observed and behaviour.threat
    assert {f["behaviour"] for f in behaviour.findings} == {"network", "process"}


def test_a_single_threat_alert_without_the_marker_is_still_a_threat(api, sandbox_pod):
    api.log = NET_SOCKET + "\n"                        # one line again: must not be lost either
    assert monitor().collect(api.core(), sandbox_pod).threat


def test_startup_noise_before_the_alert_does_not_matter(api, sandbox_pod):
    api.log = "Falco started\n" + OBSERVED + "\n"
    assert monitor().collect(api.core(), sandbox_pod).observed


def test_an_empty_log_is_not_observed(api, sandbox_pod):
    api.log = ""
    behaviour = monitor().collect(api.core(), sandbox_pod)
    assert not behaviour.observed and not behaviour.threat


def test_collect_asks_for_the_falco_pod_on_the_sandbox_pods_node(api, sandbox_pod):
    api.log = OBSERVED + "\n"
    api.requests.clear()
    monitor().collect(api.core(), sandbox_pod)
    listing = next(request for request in api.requests if "/log" not in request)
    assert "fieldSelector=spec.nodeName%3Dnode-a" in listing and "labelSelector=app.kubernetes.io%2Fname%3Dfalco" in listing
    assert any("/pods/falco-abcde/log" in request and "container=falco" in request for request in api.requests)


def test_no_running_falco_pod_on_the_node_fails_closed(api, sandbox_pod):
    api.falco_pods = [("falco-abcde", "node-a", "Pending")]
    try:
        with pytest.raises(MonitorError, match="no running Falco pod"):
            monitor().collect(api.core(), sandbox_pod)
    finally:
        api.falco_pods = [("falco-abcde", "node-a", "Running")]


def test_healthy_uses_the_real_client_too(api):
    assert monitor().healthy(api.core())
