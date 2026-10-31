"""Shared fakes for the Falco tests: realistic Falco JSON log lines and a fake Kubernetes core API.

Not a test module (pytest does not collect it). Imported by test_falco_monitor.py and test_falco_runner.py.
"""
import json
import types
from datetime import datetime, timedelta, timezone

from src.sandbox.falco_monitor import FalcoMonitor, Settings

CONTAINER = "a1b2c3d4e5f6" + "0" * 52          # what Kubernetes reports (64 hex)
SHORT = "a1b2c3d4e5f6"                          # what Falco reports (12 hex)
POD = "run-0123456789abcdef01234567-xk2lp"
OTHER_SHORT = "ffffffffffff"


def alert(rule, tags, container=SHORT, pod=POD, **fields):
    """One Falco JSON log line, shaped like real output."""
    output_fields = {"container.id": container, "k8s.pod.name": pod, "k8s.ns.name": "cloudproject-sandbox",
                     "proc.name": "python3", "proc.cmdline": "python3 -I -B -u /sandbox/script.py", **fields}
    return json.dumps({"hostname": "ip-10-0-1-5", "output": f"{rule} | ...", "priority": "Warning",
                       "rule": rule, "source": "syscall", "tags": tags, "time": "2026-10-31T01:02:03.000000000Z",
                       "output_fields": output_fields})


OBSERVED = alert("CP Sandbox Observed", ["cloudproject_sandbox", "observed"], **{"evt.type": "execve"})
NET_SOCKET = alert("CP Sandbox Network Socket", ["cloudproject_sandbox", "threat", "behaviour_network"],
                   **{"evt.type": "socket", "evt.arg.domain": "2(AF_INET)"})
NET_CONNECT = alert("CP Sandbox Network Connect", ["cloudproject_sandbox", "threat", "behaviour_network"],
                    **{"evt.type": "connect", "fd.name": "10.0.1.7:41234->93.184.216.34:80"})
EXEC_CHILD = alert("CP Sandbox Unexpected Program", ["cloudproject_sandbox", "threat", "behaviour_process"],
                   **{"evt.type": "execve", "proc.name": "echo", "proc.cmdline": "echo hi"})


def make_pod(name=POD, node="node-a", exit_code=0, reason="Completed", container_id="containerd://" + CONTAINER):
    now = datetime.now(timezone.utc)
    terminated = types.SimpleNamespace(exit_code=exit_code, reason=reason,
                                       started_at=now - timedelta(seconds=2), finished_at=now - timedelta(seconds=1))
    status = types.SimpleNamespace(
        phase="Succeeded",
        container_statuses=[types.SimpleNamespace(container_id=container_id,
                                                  state=types.SimpleNamespace(terminated=terminated))])
    return types.SimpleNamespace(metadata=types.SimpleNamespace(name=name, creation_timestamp=now - timedelta(seconds=5)),
                                 spec=types.SimpleNamespace(node_name=node), status=status)


def make_falco_pod(node="node-a", phase="Running", name="falco-abcde"):
    return types.SimpleNamespace(metadata=types.SimpleNamespace(name=name),
                                 spec=types.SimpleNamespace(node_name=node), status=types.SimpleNamespace(phase=phase))


class FakeCore:
    """Sandbox namespace and Falco namespace in one fake. falco_logs is a list: one entry per read."""

    def __init__(self, exit_code=0, reason="Completed", falco_logs=(OBSERVED,), falco_pods=None, log_error=None,
                 list_error=None):
        self.pod = make_pod(exit_code=exit_code, reason=reason)
        self.falco_pods = [make_falco_pod()] if falco_pods is None else falco_pods
        self.falco_logs, self.log_error, self.list_error = list(falco_logs), log_error, list_error
        self.falco_reads, self.falco_list_calls, self.configmaps = [], [], []

    def create_namespaced_config_map(self, namespace, body):
        self.configmaps.append(body)
        return body

    def patch_namespaced_config_map(self, *args, **kwargs):
        return None

    def delete_namespaced_config_map(self, *args, **kwargs):
        return None

    def read_namespace(self, *args, **kwargs):
        return None

    def list_namespaced_pod(self, namespace, label_selector=None, field_selector=None):
        if namespace == "falco":
            self.falco_list_calls.append((label_selector, field_selector))
            if self.list_error:
                raise self.list_error
            node = field_selector.split("=", 1)[1] if field_selector else None
            return types.SimpleNamespace(items=[p for p in self.falco_pods if node in (None, p.spec.node_name)])
        return types.SimpleNamespace(items=[self.pod])

    def read_namespaced_pod_log(self, name, namespace, container=None, since_seconds=None, limit_bytes=None):
        if namespace == "falco":
            self.falco_reads.append({"pod": name, "container": container, "since": since_seconds, "limit": limit_bytes})
            if self.log_error:
                raise self.log_error
            return self.falco_logs.pop(0) if len(self.falco_logs) > 1 else self.falco_logs[0]
        return "script output\n"


def fast_monitor(**overrides):
    sleeps = []
    settings = Settings(settle_seconds=0.0, wait_seconds=3.0, **overrides)
    clock = iter(range(1000))
    return FalcoMonitor(settings, sleep=sleeps.append, clock=lambda: next(clock)), sleeps
