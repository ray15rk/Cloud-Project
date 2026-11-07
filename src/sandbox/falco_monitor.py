"""Behavioural threat detection for EKS dry runs, backed by Falco (https://falco.org).

The sandbox's own checks (timeout, OOM kill, exit code) only see the OUTCOME of a run, and the
isolation can hide behaviour: a script that tries the network, catches the error and exits 0
looks identical to a harmless one. Falco runs on the node, outside the sandbox, and reports
what the script actually attempted at the syscall level. This module reads those reports and
turns them into a verdict for one Job's Pod.

Flow (called by EKSRunner after the Job finishes):
  1. find the Falco pod on the node where the sandbox Pod ran
  2. wait briefly so late alerts are flushed
  3. read that Falco pod's JSON log and keep alerts that belong to this Pod
  4. verdict = "observed" (Falco saw the sandbox start) + any "threat" findings

Fail closed: if Falco cannot be queried, or never reported the sandbox starting, the run is
not treated as clean. The rules that produce these alerts live in infra/falco/cloudproject_rules.yaml
and share a small contract with this file (tags and output fields), enforced by tests.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from kubernetes.client.exceptions import ApiException

from src.sandbox.kube_logs import read_pod_log_text

LOGGER = logging.getLogger(__name__)

# Contract with infra/falco/cloudproject_rules.yaml
SANDBOX_TAG = "cloudproject_sandbox"   # on every rule
OBSERVED_TAG = "observed"              # the rule proving Falco saw the sandbox start
THREAT_TAG = "threat"                  # rules whose alerts block delivery
BEHAVIOUR_PREFIX = "behaviour_"        # class of behaviour, e.g. behaviour_network

MAX_FINDINGS = 10
MAX_EVIDENCE_CHARS = 200
EVIDENCE_FIELDS = ("evt.type", "evt.arg.domain", "fd.name", "proc.name", "proc.cmdline")


class MonitorError(RuntimeError):
    """Falco could not be queried. The caller must fail closed."""


@dataclass
class Settings:
    namespace: str = "falco"
    pod_selector: str = "app.kubernetes.io/name=falco"
    container: str = "falco"
    settle_seconds: float = 3.0         # let late alerts flush after the Job ends
    wait_seconds: float = 120.0         # EKS/Falco metadata enrichment can delay the "observed" alert
    log_limit_bytes: int = 4_000_000    # more than this in the window is treated as a flood


def monitoring_enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return env.get("FALCO_ENABLED", "false").strip().lower() == "true"


def settings_from_env(env=None) -> Settings:
    env = os.environ if env is None else env
    defaults = Settings()
    return Settings(
        namespace=env.get("FALCO_NAMESPACE", defaults.namespace),
        pod_selector=env.get("FALCO_POD_SELECTOR", defaults.pod_selector),
        container=env.get("FALCO_CONTAINER", defaults.container),
        settle_seconds=float(env.get("FALCO_SETTLE_SECONDS", defaults.settle_seconds)),
        wait_seconds=float(env.get("FALCO_WAIT_SECONDS", defaults.wait_seconds)),
        log_limit_bytes=int(env.get("FALCO_LOG_LIMIT_BYTES", defaults.log_limit_bytes)),
    )


@dataclass
class Behaviour:
    observed: bool = False
    findings: list = field(default_factory=list)
    flooded: bool = False
    # What the monitor saw, so a failure can say why (set by parse_alerts and collect)
    lines: int = 0
    json_lines: int = 0
    sandbox_alerts: int = 0
    matched_alerts: int = 0
    other_alerts: list = field(default_factory=list)
    polls: int = 0
    window_seconds: int = 0
    falco_pods: list = field(default_factory=list)

    @property
    def threat(self) -> bool:
        return bool(self.findings) or self.flooded

    def summary(self) -> str:
        if self.findings:
            names = []
            for finding in self.findings:
                if finding["rule"] not in names:
                    names.append(finding["rule"])
            text = "behavioural threat detected by Falco: " + "; ".join(names)
        else:
            text = "behavioural threat detected by Falco"
        if self.flooded:
            text += " (alert flood from this node)"
        return text

    def explain_unobserved(self) -> str:
        """Why no start alert was found, naming the most likely cause first."""
        if self.lines == 0:
            hint = "the Falco log window was empty (wrong Falco pod or container, or nothing written yet)"
        elif self.json_lines == 0:
            hint = "none of the log lines parsed as JSON (is falco.json_output enabled?)"
        elif self.sandbox_alerts == 0:
            hint = ("Falco logged JSON alerts but none from the CloudProject rules (rules not loaded, "
                    "or the container/k8s fields are missing so the rules never match)")
        elif self.matched_alerts == 0:
            hint = "sandbox alerts exist, but for other Pods only: " + "; ".join(self.other_alerts)
        else:
            hint = "alerts matched this Pod but none was the start alert (is the 'observed' rule loaded?)"
        pods = ", ".join(self.falco_pods) or "no Falco pod"
        return (f"{hint}. Read {self.lines} line(s) from {pods} over a {self.window_seconds}s window "
                f"in {self.polls} poll(s)")

    def as_dict(self) -> dict:
        return {"engine": "falco", "observed": self.observed,
                "verdict": "threat" if self.threat else "clean",
                "findings": self.findings, "flooded": self.flooded,
                "diagnostics": {"log_lines": self.lines, "json_lines": self.json_lines,
                                "sandbox_alerts": self.sandbox_alerts, "matched_alerts": self.matched_alerts,
                                "other_pods": self.other_alerts, "polls": self.polls,
                                "window_seconds": self.window_seconds, "falco_pods": self.falco_pods}}


def short_container_id(container_id: str) -> str:
    """'containerd://<64 hex>' (Kubernetes) -> first 12 hex characters (what Falco reports)."""
    return (container_id or "").split("://")[-1][:12]


def _matches(fields: dict, short_id: str, pod_name: str) -> bool:
    falco_id = str(fields.get("container.id") or "")[:12]
    if short_id and falco_id and falco_id == short_id:
        return True
    return bool(pod_name) and fields.get("k8s.pod.name") == pod_name


def _evidence(fields: dict) -> str:
    parts = []
    for key in EVIDENCE_FIELDS:
        value = fields.get(key)
        if value not in (None, "", "<NA>"):
            parts.append(f"{key}={str(value)[:80]}")
    return " ".join(parts)[:MAX_EVIDENCE_CHARS]


def parse_alerts(text: str, container_id: str, pod_name: str) -> Behaviour:
    """Turn Falco's JSON log lines into a verdict for one Pod.

    Tolerant on purpose: non-JSON lines (Falco start-up logs) are skipped, and alerts for other
    Pods or other rules are ignored. Evidence strings can contain script-controlled text, so they
    are only ever displayed, truncated, and never interpreted.
    """
    short_id = short_container_id(container_id)
    behaviour = Behaviour()
    seen = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        behaviour.lines += 1
        if not line.startswith("{"):
            continue
        try:
            alert = json.loads(line)
        except ValueError:
            continue
        if not isinstance(alert, dict):
            continue
        behaviour.json_lines += 1
        tags = alert.get("tags") or []
        fields = alert.get("output_fields") or {}
        if SANDBOX_TAG not in tags or not isinstance(fields, dict):
            continue
        behaviour.sandbox_alerts += 1
        if not _matches(fields, short_id, pod_name):
            other = f"{fields.get('k8s.pod.name') or '?'} (container {str(fields.get('container.id') or '?')[:12]})"
            if other not in behaviour.other_alerts and len(behaviour.other_alerts) < 3:
                behaviour.other_alerts.append(other)
            continue
        behaviour.matched_alerts += 1
        if OBSERVED_TAG in tags:
            behaviour.observed = True
            continue
        if THREAT_TAG not in tags:
            continue
        kind = next((tag[len(BEHAVIOUR_PREFIX):] for tag in tags if tag.startswith(BEHAVIOUR_PREFIX)), "unknown")
        finding = {"rule": str(alert.get("rule", "unknown rule")), "behaviour": kind,
                   "priority": str(alert.get("priority", "")), "evidence": _evidence(fields)}
        key = (finding["rule"], finding["evidence"])
        if key in seen or len(behaviour.findings) >= MAX_FINDINGS:
            continue
        seen.add(key)
        behaviour.findings.append(finding)
    return behaviour


def _plain_call(operation, *args, **kwargs):
    return operation(*args, **kwargs)


class FalcoMonitor:
    def __init__(self, settings: Settings | None = None, sleep=time.sleep, clock=time.monotonic):
        self.settings = settings or Settings()
        self._sleep = sleep
        self._clock = clock

    def _falco_pod(self, core, node_name, call):
        try:
            pods = call(core.list_namespaced_pod, self.settings.namespace,
                        label_selector=self.settings.pod_selector,
                        field_selector=f"spec.nodeName={node_name}")
        except ApiException as exc:
            raise MonitorError(f"could not list Falco pods ({exc.status})") from exc
        running = [pod for pod in pods.items if getattr(pod.status, "phase", "") == "Running"]
        if not running:
            raise MonitorError(f"no running Falco pod on node {node_name}; "
                               "it may still be starting on a newly added node")
        return running[0]

    def _read_log(self, core, falco_pod, since_seconds, call) -> str:
        try:
            return read_pod_log_text(core, call, falco_pod.metadata.name, self.settings.namespace,
                                     container=self.settings.container, since_seconds=since_seconds,
                                     limit_bytes=self.settings.log_limit_bytes)
        except ApiException as exc:
            raise MonitorError(f"could not read Falco logs ({exc.status})") from exc

    def _settle(self, terminated):
        """Alerts reach the log a moment after the syscall; wait out the remainder of the settle time."""
        wait = self.settings.settle_seconds
        finished = getattr(terminated, "finished_at", None)
        if finished is not None:
            wait -= (datetime.now(timezone.utc) - finished).total_seconds()
        if wait > 0:
            self._sleep(min(wait, self.settings.settle_seconds))

    def collect(self, core, pod, terminated=None, call=None) -> Behaviour:
        """Return the behaviour Falco recorded for this Pod. Raises MonitorError if Falco is unreachable."""
        call = call or _plain_call
        node_name = getattr(pod.spec, "node_name", None)
        statuses = pod.status.container_statuses or []
        if not node_name or not statuses:
            raise MonitorError("sandbox Pod has no node or container status to match against")
        container_id = statuses[0].container_id or ""
        falco_pod = self._falco_pod(core, node_name, call)

        created = getattr(pod.metadata, "creation_timestamp", None)
        age = (datetime.now(timezone.utc) - created).total_seconds() if created else 300
        # Kubernetes and Falco can disagree briefly about timestamps. A narrow window has
        # excluded a fresh runner alert on EKS even though it was present in Falco's log.
        # Pod/container matching below keeps this wider window specific to this run.
        since_seconds = int(max(120, age + 120))

        self._settle(terminated)
        deadline = self._clock() + self.settings.wait_seconds
        observed_at = None
        polls = 0
        while True:
            text = self._read_log(core, falco_pod, since_seconds, call)
            polls += 1
            behaviour = parse_alerts(text, container_id, pod.metadata.name)
            behaviour.polls, behaviour.window_seconds = polls, since_seconds
            behaviour.falco_pods = [falco_pod.metadata.name]
            if len(text.encode("utf-8", errors="ignore")) >= self.settings.log_limit_bytes:
                behaviour.flooded = True      # a payload that floods the alert log is itself hostile
            if behaviour.threat:
                return behaviour
            now = self._clock()
            if behaviour.observed:
                if observed_at is None:
                    observed_at = now
                # A delayed start marker may reach the log before later syscall alerts.
                # Give those alerts one more settle window before calling the run clean.
                if now - observed_at >= self.settings.settle_seconds:
                    return behaviour
            elif now >= deadline:
                return behaviour
            self._sleep(1)

    def healthy(self, core, call=None) -> bool:
        """True when at least one Falco pod is running anywhere in the cluster."""
        call = call or _plain_call
        try:
            pods = call(core.list_namespaced_pod, self.settings.namespace,
                        label_selector=self.settings.pod_selector)
        except ApiException:
            return False
        return any(getattr(pod.status, "phase", "") == "Running" for pod in pods.items)
