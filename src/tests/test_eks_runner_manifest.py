from types import SimpleNamespace
import logging

import pytest

from src.sandbox.eks_runner import EKSRunner
import src.sandbox.eks_runner as eks_module


def test_powershell_job_runs_mounted_script_with_correct_runtime():
    runner = EKSRunner("example.dkr.ecr.ap-southeast-2.amazonaws.com/sandbox@sha256:" + "a" * 64)
    captured = {}

    class FakeBatch:
        def create_namespaced_job(self, namespace, manifest):
            captured["manifest"] = manifest
            return SimpleNamespace(metadata=SimpleNamespace(uid="job-uid"))

    runner._create_job(FakeBatch(), "run-123", "script-123", "a" * 32, "script.ps1")
    container = captured["manifest"]["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["python", "-I", "-B", "-u", "/sandbox/runner.py"]
    assert container["args"] == ["pwsh", "-NoProfile", "-File", "/sandbox/script.ps1"]
    assert container["volumeMounts"][0]["mountPath"] == "/sandbox/script.ps1"
    assert container["volumeMounts"][0]["subPath"] == "script.ps1"


def test_eks_client_failure_logs_its_stage(monkeypatch, caplog):
    runner = EKSRunner("example.dkr.ecr.ap-southeast-2.amazonaws.com/sandbox@sha256:" + "a" * 64)
    monkeypatch.setattr(runner, "_clients", lambda: (_ for _ in ()).throw(RuntimeError("client unavailable")))
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="client unavailable"):
        runner.run(b"print('ok')", "a" * 32)
    assert "failed during Kubernetes client setup" in caplog.text


def test_kubeconfig_exec_without_token_is_retried(monkeypatch):
    runner = EKSRunner("repo/sandbox@sha256:" + "a" * 64)
    attempts = []
    def load_config(*, context, client_configuration):
        attempts.append(context)
        if len(attempts) == 2:
            client_configuration.api_key["authorization"] = "Bearer test-token"
    monkeypatch.setattr(eks_module.config, "load_kube_config", load_config)
    monkeypatch.setattr(eks_module.time, "sleep", lambda _: None)

    core, batch = runner._clients()
    assert len(attempts) == 2
    assert core.api_client is batch.api_client


def test_kubeconfig_exec_without_token_fails_before_creating_a_job(monkeypatch):
    runner = EKSRunner("repo/sandbox@sha256:" + "a" * 64)
    attempts = []
    def load_config(*, context, client_configuration):
        attempts.append(context)
    monkeypatch.setattr(eks_module.config, "load_kube_config", load_config)
    monkeypatch.setattr(eks_module.time, "sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="did not provide an AWS authentication token"):
        runner._clients()
    assert len(attempts) == 3
