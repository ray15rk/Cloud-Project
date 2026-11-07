"""read_pod_log_text against the REAL Kubernetes client and a real local HTTP server.

Nothing about the client is mocked: only the API server is replaced by a small server that returns a pod
log as text/plain, the way the Kubernetes API does. This is what the unit-test fakes could not catch.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from kubernetes import client

from src.sandbox.kube_logs import read_pod_log_text


class LogServer:
    def __init__(self):
        outer = self
        self.body, self.requests = "", []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append(self.path)
                data = outer.body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def core(self):
        configuration = client.Configuration()
        configuration.host = f"http://127.0.0.1:{self.server.server_port}"
        return client.CoreV1Api(client.ApiClient(configuration))


@pytest.fixture(scope="module")
def server():
    log_server = LogServer()
    yield log_server
    log_server.server.shutdown()


def call(operation, *args, **kwargs):
    return operation(*args, **kwargs)


ALERT = json.dumps({"rule": "CP Sandbox Observed", "tags": ["cloudproject_sandbox", "observed"],
                    "output_fields": {"container.id": "a1b2c3d4e5f6"}, "time": "2026-10-31T02:15:04.123456789Z"})


@pytest.mark.parametrize("body", [
    ALERT + "\n",                                   # exactly one JSON line: the case the client mangles
    ALERT,                                          # same, no trailing newline
    ALERT + "\n" + ALERT.replace("Observed", "Network Socket") + "\n",
    "Falco starting\n" + ALERT + "\n",
    "plain text, not JSON\n",
    "[1, 2, 3]\n",                                  # a single JSON array is also reinterpreted by the client
    "",
    "caf\u00e9 \u2713 unicode survives\n",
])
def test_the_exact_log_text_comes_back(server, body):
    server.body = body
    text = read_pod_log_text(server.core(), call, "falco-x", "falco", container="falco", since_seconds=60)
    assert text == body


def test_arguments_reach_the_api_server(server):
    server.body = "x\n"
    read_pod_log_text(server.core(), call, "falco-x", "falco", container="falco", since_seconds=60, limit_bytes=1000)
    request = server.requests[-1]
    assert request.startswith("/api/v1/namespaces/falco/pods/falco-x/log?")
    assert "container=falco" in request and "sinceSeconds=60" in request and "limitBytes=1000" in request


def test_why_the_helper_exists_the_default_call_mangles_a_single_json_line(server):
    """Characterisation of the upstream behaviour. If a client upgrade fixes it, this test fails and the
    helper (and this test) can be retired."""
    server.body = ALERT + "\n"
    default = server.core().read_namespaced_pod_log("falco-x", "falco")
    assert default != server.body
    assert default.startswith("{'rule': 'CP Sandbox Observed'")      # a Python repr, single quotes
    with pytest.raises(ValueError):
        json.loads(default.splitlines()[0])
