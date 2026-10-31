"""The EKS Job must never depend on the image's ENTRYPOINT.

Without an explicit command, an image that lost its ENTRYPOINT runs the base image's default python,
which exits 0 having run nothing, so the Job reports Complete for any script. The Falco "observed"
rule also keys on /sandbox/runner.py starting as the container's PID 1.
"""
import types
from pathlib import Path

import yaml

from src.sandbox.eks_runner import EKSRunner

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ["python", "-I", "-B", "-u", "/sandbox/runner.py"]


def test_job_template_sets_an_explicit_command_and_args():
    manifest = yaml.safe_load((ROOT / "infra" / "k8s" / "sandbox-job.yaml.template").read_text(encoding="utf-8"))
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == RUNNER
    assert container["args"][-1] == "/sandbox/script.py"


def test_dockerfile_entrypoint_runs_the_runner():
    text = (ROOT / "docker" / "sandbox" / "Dockerfile").read_text(encoding="utf-8")
    assert f'ENTRYPOINT {str(RUNNER).replace(chr(39), chr(34))}' in text
    assert 'CMD ["/sandbox/script.py"]' in text


def test_created_jobs_keep_the_explicit_command():
    created = []
    batch = types.SimpleNamespace(create_namespaced_job=lambda namespace, manifest: created.append(manifest) or
                                  types.SimpleNamespace(metadata=types.SimpleNamespace(uid="u")))
    runner = EKSRunner("repo/sandbox@sha256:" + "b" * 64)
    runner._create_job(batch, "run-x", "script-x", "0123456789abcdef0123456789abcdef")
    container = created[0]["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == RUNNER and container["image"].endswith("b" * 64)
