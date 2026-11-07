"""EKSRunner with a Falco monitor: how the verdict is folded into a dry-run result.

Kubernetes and Falco are replaced with fakes, so these run anywhere.
"""
import types
from datetime import datetime, timezone

from kubernetes.client.exceptions import ApiException

import src.sandbox.eks_runner as eks
from falco_testkit import EXEC_CHILD, NET_CONNECT, NET_SOCKET, OBSERVED, FakeCore, fast_monitor
from src.sandbox.eks_runner import EKSRunner
from src.sandbox.falco_monitor import FalcoMonitor

IMAGE = "123456789012.dkr.ecr.ap-southeast-2.amazonaws.com/sandbox@sha256:" + "a" * 64
SUBMISSION = "0123456789abcdef0123456789abcdef"


class FakeBatch:
    def __init__(self, exit_code=0):
        self.exit_code, self.jobs = exit_code, []

    def create_namespaced_job(self, namespace, manifest):
        self.jobs.append(manifest)
        return types.SimpleNamespace(metadata=types.SimpleNamespace(uid="uid-1"))

    def read_namespaced_job_status(self, name, namespace):
        kind = "Complete" if self.exit_code == 0 else "Failed"
        return types.SimpleNamespace(metadata=types.SimpleNamespace(creation_timestamp=datetime.now(timezone.utc)),
                                     status=types.SimpleNamespace(conditions=[types.SimpleNamespace(type=kind)]))

    def delete_namespaced_job(self, *args, **kwargs):
        return None


def make_runner(monkeypatch, core, monitor=None, exit_code=0):
    batch = FakeBatch(exit_code)
    runner = EKSRunner(IMAGE, monitor=monitor)
    monkeypatch.setattr(runner, "_clients", lambda: (core, batch))
    monkeypatch.setattr(eks.time, "sleep", lambda s: None)
    return runner


# ------------------------------------------------------------------ EKSRunner integration

def test_without_a_monitor_nothing_changes(monkeypatch):
    core = FakeCore(falco_logs=[NET_SOCKET])
    result = make_runner(monkeypatch, core).run(b"print(1)", SUBMISSION)
    assert result["status"] == "OK" and "behaviour" not in result and core.falco_reads == []


def test_clean_run_is_ok_and_records_the_verdict(monkeypatch):
    core = FakeCore(falco_logs=[OBSERVED])
    result = make_runner(monkeypatch, core, fast_monitor()[0]).run(b"print(1)", SUBMISSION)
    assert result["status"] == "OK"
    assert result["behaviour"]["verdict"] == "clean" and result["behaviour"]["observed"] is True
    assert "monitor_ms" in result


def test_script_that_swallows_a_blocked_network_call_is_caught(monkeypatch):
    """The case the exit-code check misses: exit code 0, but the script tried the network."""
    core = FakeCore(exit_code=0, falco_logs=["\n".join([OBSERVED, NET_SOCKET, NET_CONNECT])])
    result = make_runner(monkeypatch, core, fast_monitor()[0]).run(b"...", SUBMISSION)
    assert result["returncode"] == 0
    assert result["status"] == "threat_detected"
    assert "CP Sandbox Network Socket" in result["detail"]
    assert result["behaviour"]["findings"][0]["behaviour"] == "network"


def test_a_threat_overrides_other_failure_statuses(monkeypatch):
    core = FakeCore(exit_code=124, reason="Error", falco_logs=["\n".join([OBSERVED, EXEC_CHILD])])
    result = make_runner(monkeypatch, core, fast_monitor()[0], exit_code=124).run(b"...", SUBMISSION)
    assert result["status"] == "threat_detected"


def test_unobserved_run_is_never_called_clean(monkeypatch):
    core = FakeCore(falco_logs=["Falco is up but never saw this container"])
    result = make_runner(monkeypatch, core, fast_monitor()[0]).run(b"print(1)", SUBMISSION)
    assert result["status"] == "infrastructure_error"
    assert "did not report the sandbox starting" in result["detail"]


def test_an_already_failed_run_keeps_its_own_status_when_unobserved(monkeypatch):
    core = FakeCore(exit_code=7, reason="Error", falco_logs=["nothing"])
    result = make_runner(monkeypatch, core, fast_monitor()[0], exit_code=7).run(b"raise SystemExit(7)", SUBMISSION)
    assert result["status"] == "error"


def test_unreachable_falco_fails_closed(monkeypatch):
    core = FakeCore(log_error=ApiException(status=403, reason="Forbidden"))
    result = make_runner(monkeypatch, core, fast_monitor()[0]).run(b"print(1)", SUBMISSION)
    assert result["status"] == "infrastructure_error"
    assert "behaviour monitor unavailable" in result["detail"]
    assert result["behaviour"]["verdict"] == "unknown"


def test_unreachable_falco_does_not_hide_an_existing_failure(monkeypatch):
    core = FakeCore(exit_code=7, reason="Error", list_error=ApiException(status=500, reason="boom"))
    result = make_runner(monkeypatch, core, fast_monitor()[0], exit_code=7).run(b"x", SUBMISSION)
    assert result["status"] == "error"


def test_monitor_healthy_is_none_without_a_monitor(monkeypatch):
    runner = make_runner(monkeypatch, FakeCore())
    assert runner.monitor_healthy() is None
    runner.monitor = fast_monitor()[0]
    assert runner.monitor_healthy() is True


def test_configured_runner_enables_the_monitor_only_when_asked(monkeypatch):
    monkeypatch.setenv("EKS_IMAGE", IMAGE)
    monkeypatch.delenv("FALCO_ENABLED", raising=False)
    assert eks.configured_runner().monitor is None
    monkeypatch.setenv("FALCO_ENABLED", "true")
    assert isinstance(eks.configured_runner().monitor, FalcoMonitor)


def test_sandbox_output_that_is_one_json_line_is_not_mangled(monkeypatch):
    core = FakeCore(sandbox_log='{"ok": true, "n": 1}\n')
    result = make_runner(monkeypatch, core).run(b"print('{\"ok\": true, \"n\": 1}')", SUBMISSION)
    assert result["stdout"] == '{"ok": true, "n": 1}\n'      # not "{'ok': True, 'n': 1}"


def test_unobserved_failure_explains_itself(monkeypatch):
    core = FakeCore(falco_logs=["Falco is up but logged nothing for this Pod"])
    result = make_runner(monkeypatch, core, fast_monitor()[0]).run(b"print(1)", SUBMISSION)
    assert result["status"] == "infrastructure_error"
    assert "none of the log lines parsed as JSON" in result["detail"]
    assert result["behaviour"]["diagnostics"]["log_lines"] == 1
