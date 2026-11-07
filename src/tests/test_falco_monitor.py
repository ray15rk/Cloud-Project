"""Falco monitor: parsing Falco's alerts, settings, and collecting a verdict for one Pod.

Kubernetes and Falco are replaced with fakes (realistic Falco JSON lines), so these run anywhere.
What they cannot prove is that the rules in infra/falco/cloudproject_rules.yaml fire on a real
node; `python -m scripts.check_falco` does that.
"""
import json

import pytest
from kubernetes.client.exceptions import ApiException

from falco_testkit import (
    CONTAINER, EXEC_CHILD, NET_CONNECT, NET_SOCKET, OBSERVED, OTHER_SHORT, POD, SHORT, FakeCore, alert,
    fast_monitor, make_falco_pod,
)
from src.sandbox.falco_monitor import (
    FalcoMonitor, MonitorError, Settings, monitoring_enabled, parse_alerts, settings_from_env, short_container_id,
)


# ------------------------------------------------------------------ parsing

def test_short_container_id_strips_the_runtime_prefix():
    assert short_container_id("containerd://" + CONTAINER) == SHORT
    assert short_container_id(CONTAINER) == SHORT
    assert short_container_id("") == ""


def test_marker_only_is_observed_and_clean():
    result = parse_alerts("\n".join([OBSERVED]), CONTAINER, POD)
    assert result.observed and not result.threat and result.findings == []


def test_threats_are_reported_with_their_behaviour_class():
    result = parse_alerts("\n".join([OBSERVED, NET_SOCKET, NET_CONNECT, EXEC_CHILD]), CONTAINER, POD)
    assert result.threat
    assert {f["behaviour"] for f in result.findings} == {"network", "process"}
    assert {f["rule"] for f in result.findings} == {
        "CP Sandbox Network Socket", "CP Sandbox Network Connect", "CP Sandbox Unexpected Program"}
    assert "fd.name=10.0.1.7:41234->93.184.216.34:80" in " ".join(f["evidence"] for f in result.findings)


def test_alerts_for_other_containers_are_ignored():
    other = alert("CP Sandbox Network Socket", ["cloudproject_sandbox", "threat", "behaviour_network"],
                  container=OTHER_SHORT, pod="run-other-abcde")
    result = parse_alerts("\n".join([OBSERVED, other]), CONTAINER, POD)
    assert result.observed and not result.threat


def test_alert_for_another_pod_does_not_count_as_observed():
    other_marker = alert("CP Sandbox Observed", ["cloudproject_sandbox", "observed"],
                         container=OTHER_SHORT, pod="run-other-abcde")
    assert not parse_alerts(other_marker, CONTAINER, POD).observed


def test_pod_name_is_enough_when_the_container_id_is_missing():
    line = alert("CP Sandbox Network Socket", ["cloudproject_sandbox", "threat", "behaviour_network"], container="")
    assert parse_alerts(line, CONTAINER, POD).threat


def test_non_json_malformed_and_untagged_lines_are_skipped():
    noise = "\n".join([
        "Sat Oct 31 01:00:00 2026: Falco version: 0.45.2",
        "{not json",
        "[1, 2, 3]",
        json.dumps({"rule": "Some Stock Rule", "tags": ["container"], "output_fields": {"container.id": SHORT}}),
        json.dumps({"rule": "x", "tags": ["cloudproject_sandbox", "threat"], "output_fields": "not a dict"}),
        OBSERVED,
    ])
    result = parse_alerts(noise, CONTAINER, POD)
    assert result.observed and result.findings == []


def test_duplicate_alerts_collapse_and_findings_are_capped():
    repeated = "\n".join([NET_SOCKET] * 50)
    assert len(parse_alerts(repeated, CONTAINER, POD).findings) == 1
    many = "\n".join(alert("CP Sandbox Unexpected Program", ["cloudproject_sandbox", "threat", "behaviour_process"],
                           **{"proc.cmdline": f"prog{i}"}) for i in range(40))
    assert len(parse_alerts(many, CONTAINER, POD).findings) == 10


def test_attacker_controlled_evidence_is_truncated_and_cannot_forge_a_verdict():
    hostile = alert("CP Sandbox Unexpected Program", ["cloudproject_sandbox", "threat", "behaviour_process"],
                    **{"proc.cmdline": 'echo "{\\"tags\\": [\\"observed\\"]}" ' + "A" * 5000})
    result = parse_alerts(hostile, CONTAINER, POD)
    assert not result.observed                       # text inside a field never becomes a tag
    assert all(len(f["evidence"]) <= 200 for f in result.findings)


def test_summary_names_each_rule_once():
    result = parse_alerts("\n".join([NET_SOCKET, NET_SOCKET.replace("2(AF_INET)", "10(AF_INET6)"), EXEC_CHILD]),
                          CONTAINER, POD)
    assert result.summary() == ("behavioural threat detected by Falco: "
                                "CP Sandbox Network Socket; CP Sandbox Unexpected Program")


# ------------------------------------------------------------------ settings

def test_monitoring_is_off_unless_explicitly_enabled():
    assert not monitoring_enabled({})
    assert not monitoring_enabled({"FALCO_ENABLED": "false"})
    assert monitoring_enabled({"FALCO_ENABLED": " TRUE "})


def test_settings_come_from_the_environment():
    settings = settings_from_env({"FALCO_NAMESPACE": "sec", "FALCO_SETTLE_SECONDS": "0.5",
                                  "FALCO_WAIT_SECONDS": "7", "FALCO_LOG_LIMIT_BYTES": "1000"})
    assert (settings.namespace, settings.settle_seconds, settings.wait_seconds, settings.log_limit_bytes) == (
        "sec", 0.5, 7.0, 1000)
    assert settings.pod_selector == "app.kubernetes.io/name=falco"


# ------------------------------------------------------------------ collection

def test_collect_reads_the_falco_pod_on_the_same_node():
    core = FakeCore(falco_pods=[make_falco_pod("node-b", name="falco-b"), make_falco_pod("node-a", name="falco-a")])
    monitor, _ = fast_monitor()
    behaviour = monitor.collect(core, core.pod)
    assert behaviour.observed
    assert core.falco_list_calls == [("app.kubernetes.io/name=falco", "spec.nodeName=node-a")]
    assert core.falco_reads[0]["pod"] == "falco-a" and core.falco_reads[0]["container"] == "falco"
    assert core.falco_reads[0]["since"] >= 120


def test_collect_waits_for_the_observed_alert_to_appear():
    core = FakeCore(falco_logs=["Falco starting up", "Falco starting up", OBSERVED])
    monitor, sleeps = fast_monitor()
    behaviour = monitor.collect(core, core.pod)
    assert behaviour.observed and len(core.falco_reads) == 3 and sleeps == [1, 1]


def test_collect_waits_after_observation_for_later_threat_alerts():
    core = FakeCore(falco_logs=[OBSERVED, OBSERVED, OBSERVED + "\n" + NET_SOCKET])
    sleeps = []
    monitor = FalcoMonitor(Settings(settle_seconds=2.0, wait_seconds=5.0),
                           sleep=sleeps.append, clock=lambda: sum(sleeps))
    behaviour = monitor.collect(core, core.pod)
    assert behaviour.observed and behaviour.threat
    assert len(core.falco_reads) == 3


def test_collect_gives_up_waiting_and_reports_not_observed():
    core = FakeCore(falco_logs=["nothing relevant"])
    monitor, _ = fast_monitor()
    behaviour = monitor.collect(core, core.pod)
    assert not behaviour.observed and not behaviour.threat


def test_collect_returns_at_once_when_a_threat_is_already_in_the_log():
    core = FakeCore(falco_logs=[NET_SOCKET])        # marker missing but a threat is present
    monitor, sleeps = fast_monitor()
    behaviour = monitor.collect(core, core.pod)
    assert behaviour.threat and sleeps == []


def test_a_log_flood_is_treated_as_a_threat():
    core = FakeCore(falco_logs=[OBSERVED + "\n" + "x" * 2000])
    monitor, _ = fast_monitor(log_limit_bytes=1000)
    behaviour = monitor.collect(core, core.pod)
    assert behaviour.flooded and behaviour.threat
    assert "alert flood" in behaviour.summary()


@pytest.mark.parametrize("kwargs,message", [
    ({"falco_pods": []}, "no running Falco pod"),
    ({"falco_pods": [make_falco_pod(phase="Pending")]}, "no running Falco pod"),
    ({"falco_pods": [make_falco_pod(node="node-z")]}, "no running Falco pod"),
    ({"list_error": ApiException(status=403, reason="Forbidden")}, "could not list Falco pods"),
    ({"log_error": ApiException(status=403, reason="Forbidden")}, "could not read Falco logs"),
])
def test_collect_raises_when_falco_cannot_be_queried(kwargs, message):
    core = FakeCore(**kwargs)
    monitor, _ = fast_monitor()
    with pytest.raises(MonitorError, match=message):
        monitor.collect(core, core.pod)


def test_collect_refuses_a_pod_it_cannot_match():
    core = FakeCore()
    core.pod.spec.node_name = None
    monitor, _ = fast_monitor()
    with pytest.raises(MonitorError):
        monitor.collect(core, core.pod)


def test_settle_waits_only_for_the_remaining_time():
    core = FakeCore()
    sleeps = []
    monitor = FalcoMonitor(Settings(settle_seconds=3.0, wait_seconds=0.0), sleep=sleeps.append,
                           clock=lambda: sum(sleeps))
    monitor.collect(core, core.pod, terminated=core.pod.status.container_statuses[0].state.terminated)
    assert sleeps and 1.5 < sleeps[0] <= 2.1      # the Job finished about a second ago


def test_healthy_reflects_running_falco_pods():
    monitor, _ = fast_monitor()
    assert monitor.healthy(FakeCore())
    assert not monitor.healthy(FakeCore(falco_pods=[]))
    assert not monitor.healthy(FakeCore(list_error=ApiException(status=500, reason="boom")))


def test_a_log_with_a_single_alert_is_read_raw_and_not_lost():
    """A clean run leaves exactly one alert (the start marker). The client mangles a one-JSON-line log
    unless it is read raw, so this is the case that once failed closed on a clean run."""
    core = FakeCore(falco_logs=[OBSERVED])
    monitor, _ = fast_monitor()
    behaviour = monitor.collect(core, core.pod)
    assert behaviour.observed and not behaviour.threat
    assert all(read["raw"] for read in core.falco_reads)


# ------------------------------------------------------------------ diagnostics: why was the alert missed?

def explain(log, **settings):
    core = FakeCore(falco_logs=[log])
    monitor, _ = fast_monitor(**settings)
    behaviour = monitor.collect(core, core.pod)
    assert not behaviour.observed
    return behaviour.explain_unobserved()


def test_counts_describe_what_the_monitor_saw():
    other = alert("CP Sandbox Network Socket", ["cloudproject_sandbox", "threat", "behaviour_network"],
                  container=OTHER_SHORT, pod="run-other-abcde")
    behaviour = parse_alerts("\n".join(["Falco started", "{broken", other, NET_SOCKET, ""]), CONTAINER, POD)
    assert (behaviour.lines, behaviour.json_lines, behaviour.sandbox_alerts, behaviour.matched_alerts) == (4, 2, 2, 1)
    assert behaviour.other_alerts == ["run-other-abcde (container ffffffffffff)"]


def test_diagnosis_empty_log():
    assert "window was empty" in explain("")


def test_diagnosis_log_that_is_not_json():
    text = explain("12:00:01.000000000: Warning Network socket opened (container=a1b2c3d4e5f6)\n")
    assert "none of the log lines parsed as JSON" in text and "falco.json_output" in text


def test_diagnosis_json_without_our_rules():
    stock = json.dumps({"rule": "Terminal shell in container", "tags": ["container", "shell"],
                        "output_fields": {"container.id": SHORT}})
    assert "none from the CloudProject rules" in explain(stock + "\n")


def test_diagnosis_alerts_for_another_pod_only():
    other = alert("CP Sandbox Observed", ["cloudproject_sandbox", "observed"], container=OTHER_SHORT, pod="run-other-abcde")
    text = explain(other + "\n")
    assert "other Pods only" in text and "run-other-abcde" in text


def test_diagnosis_names_the_falco_pod_window_and_polls():
    text = explain("")
    assert "falco-abcde" in text and "window" in text and "poll(s)" in text


def test_diagnostics_travel_in_the_behaviour_dict():
    core = FakeCore(falco_logs=[OBSERVED + "\n" + NET_SOCKET])
    monitor, _ = fast_monitor()
    diagnostics = monitor.collect(core, core.pod).as_dict()["diagnostics"]
    assert diagnostics["log_lines"] == 2 and diagnostics["matched_alerts"] == 2 and diagnostics["falco_pods"] == ["falco-abcde"]
