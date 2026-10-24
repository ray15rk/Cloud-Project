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


NAMESPACE = "cloudproject-sandbox"
TEMPLATE = Path(__file__).resolve().parents[2] / "infra" / "k8s" / "sandbox-job.yaml.template"
LOGGER = logging.getLogger(__name__)


class EKSRunner:
    def __init__(self, image: str, context: str = "cloudproject-sandbox-lab"):
        if "@sha256:" not in image:
            raise ValueError("EKS_IMAGE must use an immutable image digest")
        self.image = image
        self.context = context

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

    def _create_configmap(self, core, name, payload, submission_id):
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
            "binaryData": {"script.py": base64.b64encode(payload).decode("ascii")},
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

    def _create_job(self, batch, name, configmap_name, submission_id):
        manifest = yaml.safe_load(TEMPLATE.read_text())
        manifest["metadata"].update(name=name, labels={
            "app": "cloudproject-sandbox", "submission-id": submission_id,
        })
        manifest["spec"]["template"]["spec"]["containers"][0]["image"] = self.image
        manifest["spec"]["template"]["spec"]["volumes"][0]["configMap"]["name"] = configmap_name
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
            output = self._call(core.read_namespaced_pod_log, pod.metadata.name, NAMESPACE)
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
        return result

    def run(self, payload: bytes, submission_id: str) -> dict:
        if len(payload) > 65536:
            raise ValueError("payload exceeds 64 KiB")
        if len(submission_id) != 32 or any(character not in "0123456789abcdef" for character in submission_id):
            raise ValueError("submission ID must be a lowercase UUID hex value")
        started = time.monotonic()
        job_name = f"run-{submission_id[:24]}"
        configmap_name = f"script-{submission_id[:24]}"
        core, batch = self._clients()
        created_configmap = False
        job_owned = False
        try:
            self._create_configmap(core, configmap_name, payload, submission_id)
            created_configmap = True
            job = self._create_job(batch, job_name, configmap_name, submission_id)
            job_owned = True
            owner = {"apiVersion": "batch/v1", "kind": "Job", "name": job_name,
                     "uid": job.metadata.uid, "controller": False, "blockOwnerDeletion": False}
            self._call(core.patch_namespaced_config_map, configmap_name, NAMESPACE,
                       {"metadata": {"ownerReferences": [owner]}})
            return self._result(core, batch, job_name, started)
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


def configured_runner():
    image = os.environ.get("EKS_IMAGE", "")
    return EKSRunner(image, os.environ.get("EKS_CONTEXT", "cloudproject-sandbox-lab"))
