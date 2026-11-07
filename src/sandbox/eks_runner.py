"""Run verified scripts as disposable, network-isolated EKS Jobs."""

import base64
import hashlib
import logging
import os
import random
import time
from pathlib import Path

import yaml
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from src.sandbox.falco_monitor import FalcoMonitor, MonitorError, monitoring_enabled, settings_from_env
from src.sandbox.kube_logs import read_pod_log_text


NAMESPACE = "cloudproject-sandbox"
TEMPLATE = Path(__file__).resolve().parents[2] / "infra" / "k8s" / "sandbox-job.yaml.template"
LOGGER = logging.getLogger(__name__)
SCRIPT_EXTENSIONS = {"python": "py", "bash": "sh", "powershell": "ps1"}


class EKSRunner:
    def __init__(self, image: str, context: str = "cloudproject-sandbox-sydney", monitor: FalcoMonitor | None = None):
        if "@sha256:" not in image:
            raise ValueError("EKS_IMAGE must use an immutable image digest")
        self.image = image
        self.context = context
        self.monitor = monitor   # None = outcome-only checks (no behavioural detection)

    def _clients(self):
        configuration = client.Configuration()
        config.load_kube_config(context=self.context, client_configuration=configuration)
        api = client.ApiClient(configuration=configuration)
        return client.CoreV1Api(api), client.BatchV1Api(api)

    @staticmethod
    def _call(operation, *args, **kwargs):
        for attempt in range(4):
            try:
                return operation(*args, **kwargs)
            except ApiException as exc:
                if exc.status not in {429, 500, 502, 503, 504} or attempt == 3:
                    raise
                time.sleep(min(4, 0.4 * 2**attempt) + random.uniform(0, 0.2))

    @staticmethod
    def _existing_or_raise(read_operation, name, error):
        try:
            return read_operation(name, NAMESPACE)
        except ApiException as read_error:
            if read_error.status == 404:
                raise error
            raise

    def _create_configmap(self, core, name, payload, submission_id, script_name):
        digest = hashlib.sha256(payload).hexdigest()
        body = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": name,
                "namespace": NAMESPACE,
                "labels": {"app": "cloudproject-sandbox", "submission-id": submission_id},
                "annotations": {"cloudproject/payload-sha256": digest},
            },
            "binaryData": {script_name: base64.b64encode(payload).decode("ascii")},
            "immutable": True,
        }
        try:
            return self._call(core.create_namespaced_config_map, NAMESPACE, body)
        except Exception as exc:
            existing = self._existing_or_raise(core.read_namespaced_config_map, name, exc)
            if (existing.metadata.annotations.get("cloudproject/payload-sha256") != digest
                    or existing.metadata.labels.get("submission-id") != submission_id):
                raise RuntimeError("existing payload ConfigMap does not match submission") from exc
            return existing

    def _create_job(self, batch, name, configmap_name, submission_id, script_name="script.py"):
        manifest = yaml.safe_load(TEMPLATE.read_text())
        manifest["metadata"].update(name=name, labels={
            "app": "cloudproject-sandbox", "submission-id": submission_id,
        })
        container = manifest["spec"]["template"]["spec"]["containers"][0]
        container["image"] = self.image
        commands = {
            "py": ["python3", "-I", "-B", "-u", f"/sandbox/{script_name}"],
            "sh": ["bash", f"/sandbox/{script_name}"],
            "ps1": ["pwsh", "-NoProfile", "-File", f"/sandbox/{script_name}"],
        }
        container["args"] = commands[script_name.rsplit(".", 1)[-1]]
        container["volumeMounts"][0]["mountPath"] = f"/sandbox/{script_name}"
        container["volumeMounts"][0]["subPath"] = script_name
        manifest["spec"]["template"]["spec"]["volumes"][0]["configMap"]["name"] = configmap_name
        manifest["spec"]["template"]["spec"]["volumes"][0]["configMap"]["items"] = [
            {"key": script_name, "path": script_name}]
        try:
            return self._call(batch.create_namespaced_job, NAMESPACE, manifest)
        except Exception as exc:
            existing = self._existing_or_raise(batch.read_namespaced_job, name, exc)
            if existing.metadata.labels.get("submission-id") != submission_id:
                raise RuntimeError("existing Job does not belong to submission") from exc
            return existing

    def _result(self, core, batch, name, started):
        deadline = time.monotonic() + 330
        while time.monotonic() < deadline:
            job = self._call(batch.read_namespaced_job_status, name, NAMESPACE)
            conditions = {condition.type: condition for condition in (job.status.conditions or [])}
            if "Complete" in conditions or "Failed" in conditions:
                break
            time.sleep(2)
        else:
            return {"status": "infrastructure_error", "detail": "EKS Job status deadline exceeded"}

        pods = self._call(core.list_namespaced_pod, NAMESPACE, label_selector=f"job-name={name}")
        if not pods.items:
            return {"status": "infrastructure_error", "detail": "EKS Job has no Pod"}
        if len(pods.items) != 1:
            return {"status": "infrastructure_error", "detail": "EKS Job started multiple Pods"}
        pod = pods.items[0]
        container_statuses = pod.status.container_statuses or []
        terminated = container_statuses[0].state.terminated if container_statuses else None
        if not terminated:
            return {"status": "infrastructure_error", "detail": "EKS Pod has no termination status"}

        try:
            output = read_pod_log_text(core, self._call, pod.metadata.name, NAMESPACE)
        except ApiException:
            output = ""
        output = output[:65536]
        result = {"job_name": name, "stdout": output, "returncode": terminated.exit_code,
                  "cloud_ms": round((time.monotonic() - started) * 1000, 2)}
        if job.metadata.creation_timestamp and terminated.started_at:
            result["pod_startup_ms"] = round((terminated.started_at - job.metadata.creation_timestamp).total_seconds() * 1000, 2)
        if terminated.started_at and terminated.finished_at:
            result["execution_ms"] = round((terminated.finished_at - terminated.started_at).total_seconds() * 1000, 2)
        if terminated.reason == "OOMKilled":
            result.update(status="oom_killed", detail="EKS Pod exceeded its 128Mi memory limit")
        elif terminated.exit_code == 124:
            result.update(status="timeout", detail="Script exceeded its 10-second execution deadline")
        elif terminated.exit_code == 0 and "Complete" in conditions:
            result.update(status="OK")
        else:
            result.update(status="error", detail=output[-16000:] or terminated.reason or "script failed")
        if self.monitor is not None:
            self._apply_behaviour(core, pod, terminated, result)
        return result

    def _apply_behaviour(self, core, pod, terminated, result):
        """Fold Falco's verdict into the result. A threat overrides any outcome, because it explains
        the run better than an exit code does; an unobserved or unreadable run is never called clean."""
        started = time.monotonic()
        try:
            behaviour = self.monitor.collect(core, pod, terminated, call=self._call)
        except MonitorError as exc:
            LOGGER.warning("Falco could not vet %s: %s", pod.metadata.name, exc)
            result["behaviour"] = {"engine": "falco", "verdict": "unknown", "error": str(exc)}
            if result["status"] == "OK":
                result.update(status="infrastructure_error", detail=f"behaviour monitor unavailable: {exc}")
            return
        finally:
            result["monitor_ms"] = round((time.monotonic() - started) * 1000, 2)
        result["behaviour"] = behaviour.as_dict()
        if behaviour.threat:
            result.update(status="threat_detected", detail=behaviour.summary())
        elif not behaviour.observed and result["status"] == "OK":
            result.update(status="infrastructure_error",
                          detail="Falco did not report the sandbox starting, so the run cannot be called clean ("
                                 + behaviour.explain_unobserved() + ")")

    def run(self, payload: bytes, submission_id: str, language: str = "python") -> dict:
        if len(payload) > 65536:
            raise ValueError("payload exceeds 64 KiB")
        if len(submission_id) != 32 or any(character not in "0123456789abcdef" for character in submission_id):
            raise ValueError("submission ID must be a lowercase UUID hex value")
        if language not in SCRIPT_EXTENSIONS:
            raise ValueError("unsupported payload language")
        started = time.monotonic()
        job_name = f"run-{submission_id[:24]}"
        configmap_name = f"script-{submission_id[:24]}"
        script_name = f"script.{SCRIPT_EXTENSIONS[language]}"
        stage = "Kubernetes client setup"
        try:
            core, batch = self._clients()
        except Exception:
            LOGGER.exception("EKS submission %s failed during %s", submission_id, stage)
            raise
        created_configmap = False
        job_owned = False
        try:
            stage = "payload ConfigMap creation"
            self._create_configmap(core, configmap_name, payload, submission_id, script_name)
            created_configmap = True
            stage = "Job creation"
            job = self._create_job(batch, job_name, configmap_name, submission_id, script_name)
            job_owned = True
            owner = {"apiVersion": "batch/v1", "kind": "Job", "name": job_name,
                     "uid": job.metadata.uid, "controller": False, "blockOwnerDeletion": False}
            stage = "ConfigMap owner update"
            self._call(core.patch_namespaced_config_map, configmap_name, NAMESPACE,
                       {"metadata": {"ownerReferences": [owner]}})
            stage = "Job result collection"
            return self._result(core, batch, job_name, started)
        except Exception:
            LOGGER.exception("EKS submission %s failed during %s", submission_id, stage)
            raise
        finally:
            if job_owned:
                try:
                    self._call(batch.delete_namespaced_job, job_name, NAMESPACE,
                               body=client.V1DeleteOptions(propagation_policy="Background"))
                except ApiException as exc:
                    if exc.status != 404:
                        LOGGER.warning("Could not delete EKS Job %s: %s", job_name, exc)
            if created_configmap:
                try:
                    self._call(core.delete_namespaced_config_map, configmap_name, NAMESPACE)
                except ApiException as exc:
                    if exc.status != 404:
                        LOGGER.warning("Could not delete payload ConfigMap %s: %s", configmap_name, exc)

    def healthy(self) -> bool:
        try:
            core, _ = self._clients()
            self._call(core.read_namespace, NAMESPACE)
            return True
        except Exception:
            return False

    def monitor_healthy(self) -> bool | None:
        """None when behaviour monitoring is off, else whether a Falco pod is running."""
        if self.monitor is None:
            return None
        try:
            core, _ = self._clients()
            return self.monitor.healthy(core, self._call)
        except Exception:
            return False


def configured_runner():
    image = os.environ.get("EKS_IMAGE", "")
    monitor = FalcoMonitor(settings_from_env()) if monitoring_enabled() else None
    return EKSRunner(image, os.environ.get("EKS_CONTEXT", "cloudproject-sandbox-sydney"), monitor)
