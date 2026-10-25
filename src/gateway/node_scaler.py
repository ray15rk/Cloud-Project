"""Operator-side EKS node scaler for labs without Pod Identity or IRSA IAM roles.

This process uses the operator's existing AWS credentials. It is not the
Kubernetes Cluster Autoscaler and only runs while the local gateway runs.
"""

import logging
import subprocess
import threading
import time

from kubernetes import client, config


LOGGER = logging.getLogger(__name__)
NAMESPACE = "cloudproject-sandbox"
LABEL = "app=cloudproject-sandbox"
RESOURCE_SHORTAGES = ("Insufficient cpu", "Insufficient memory", "Too many pods")


class NodeScaler:
    def __init__(self, cluster="cloudproject-sandbox-lab", nodegroup="sandbox-workers",
                 context="cloudproject-sandbox-lab", region="us-east-1", idle_seconds=300,
                 poll_seconds=20):
        self.cluster = cluster
        self.nodegroup = nodegroup
        self.context = context
        self.region = region
        self.idle_seconds = idle_seconds
        self.poll_seconds = poll_seconds
        self._thread = None
        self._lock = threading.Lock()
        self._idle_since = None
        self._status = {"state": "stopped", "desired": None, "last_action": None}

    def status(self):
        with self._lock:
            return dict(self._status)

    def _set_status(self, **changes):
        with self._lock:
            self._status.update(changes)

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, daemon=True, name="eks-node-scaler")
            self._status["state"] = "starting"
            self._thread.start()

    def _pods(self):
        configuration = client.Configuration()
        config.load_kube_config(context=self.context, client_configuration=configuration)
        api = client.CoreV1Api(client.ApiClient(configuration=configuration))
        return api.list_namespaced_pod(NAMESPACE, label_selector=LABEL).items

    def _aws(self, *args):
        result = subprocess.run(["aws", "eks", *args, "--region", self.region,
                                 "--output", "json"], capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise RuntimeError(f"AWS EKS command failed ({result.returncode})")
        return result.stdout

    def _nodegroup(self):
        import json
        return json.loads(self._aws("describe-nodegroup", "--cluster-name", self.cluster,
                                   "--nodegroup-name", self.nodegroup))["nodegroup"]

    def _set_desired(self, desired):
        self._aws("update-nodegroup-config", "--cluster-name", self.cluster,
                  "--nodegroup-name", self.nodegroup, "--scaling-config", f"desiredSize={desired}")

    def step(self, now=None):
        """Apply one bounded decision; failures leave the current node count alone."""
        now = time.monotonic() if now is None else now
        pods = self._pods()
        pending_resources = any(
            pod.status.phase == "Pending" and any(
                condition.type == "PodScheduled" and condition.status == "False"
                and condition.reason == "Unschedulable"
                and any(shortage in (condition.message or "") for shortage in RESOURCE_SHORTAGES)
                for condition in (pod.status.conditions or []))
            for pod in pods)
        active = any(pod.status.phase in {"Pending", "Running", "Unknown"} for pod in pods)
        if active:
            self._idle_since = None
        elif self._idle_since is None:
            self._idle_since = now

        group = self._nodegroup()
        scaling = group["scalingConfig"]
        desired = scaling["desiredSize"]
        if scaling["minSize"] != 1 or scaling["maxSize"] != 2:
            raise RuntimeError("Node group must have minSize=1 and maxSize=2")
        self._set_status(state="watching", desired=desired)
        if group["status"] != "ACTIVE":
            return
        if pending_resources and desired == 1:
            self._set_desired(2)
            self._set_status(state="scaling_up", desired=2, last_action="scale_up")
            LOGGER.info("Resource-pending sandbox Pod; requested two EKS nodes")
        elif desired == 2 and not active and self._idle_since is not None and now - self._idle_since >= self.idle_seconds:
            self._set_desired(1)
            self._set_status(state="scaling_down", desired=1, last_action="scale_down")
            LOGGER.info("Sandbox idle; requested one EKS node")

    def _loop(self):
        while True:
            try:
                self.step()
            except Exception:
                self._set_status(state="error")
                LOGGER.exception("EKS node scaler check failed")
            time.sleep(self.poll_seconds)
