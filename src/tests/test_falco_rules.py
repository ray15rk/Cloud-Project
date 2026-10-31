"""Guards for infra/falco/*: the rules, the Helm values and the manifests.

Falco refuses to load a rule that uses an unknown field or event, and the gateway silently depends on
tags and output fields. These tests catch both kinds of drift without needing Falco installed. They
do NOT prove the rules fire on a real node; `python -m scripts.check_falco` does that.
"""
import re
from pathlib import Path

import pytest
import yaml

from src.sandbox.falco_monitor import BEHAVIOUR_PREFIX, OBSERVED_TAG, SANDBOX_TAG, THREAT_TAG

FALCO_DIR = Path(__file__).resolve().parents[2] / "infra" / "falco"

# Every field and event name below is used by the official rules in github.com/falcosecurity/rules
# (falco_rules.yaml, falco-incubating_rules.yaml, falco-sandbox_rules.yaml) as of Falco 0.45. Add a
# name here only after confirming Falco accepts it.
KNOWN_FIELDS = {
    "container.id", "k8s.ns.name", "k8s.pod.name", "evt.type", "evt.arg.domain", "evt.is_open_write",
    "evt.res", "fd.typechar", "fd.name", "proc.name", "proc.vpid", "proc.cmdline", "proc.pname",
}
KNOWN_EVENTS = {"execve", "execveat", "open", "openat", "openat2", "socket", "connect",
                "ptrace", "setns", "unshare", "bpf", "init_module", "finit_module"}
PRIORITIES = {"EMERGENCY", "ALERT", "CRITICAL", "ERROR", "WARNING", "NOTICE", "INFORMATIONAL", "DEBUG"}


@pytest.fixture(scope="module")
def parsed():
    items = yaml.safe_load((FALCO_DIR / "cloudproject_rules.yaml").read_text(encoding="utf-8"))
    return {
        "rules": [i for i in items if "rule" in i],
        "macros": {i["macro"]: i["condition"] for i in items if "macro" in i},
        "lists": {i["list"]: i["items"] for i in items if "list" in i},
    }


def collapse(text):
    return " ".join(str(text).split())


def expand(condition, macros, depth=0):
    """Inline macros so a rule's full condition can be inspected."""
    assert depth < 10, "macro cycle"
    def replace(match):
        name = match.group(0)
        return f"({expand(collapse(macros[name]), macros, depth + 1)})" if name in macros else name
    return re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\b", replace, collapse(condition))


def test_every_rule_is_well_formed(parsed):
    assert parsed["rules"], "no rules found"
    for rule in parsed["rules"]:
        for key in ("rule", "desc", "condition", "output", "priority", "tags"):
            assert rule.get(key), f"{rule.get('rule')}: missing {key}"
        assert rule["priority"] in PRIORITIES
        assert isinstance(rule["tags"], list)


def test_rule_names_are_unique(parsed):
    names = [r["rule"] for r in parsed["rules"]]
    assert len(names) == len(set(names))


def test_every_rule_carries_the_sandbox_tag(parsed):
    assert all(SANDBOX_TAG in r["tags"] for r in parsed["rules"])


def test_exactly_one_rule_proves_observation(parsed):
    observed = [r for r in parsed["rules"] if OBSERVED_TAG in r["tags"]]
    assert len(observed) == 1
    assert THREAT_TAG not in observed[0]["tags"]
    assert "proc.vpid=1" in expand(observed[0]["condition"], parsed["macros"]).replace(" ", "")
    assert "/sandbox/runner.py" in observed[0]["condition"]


def test_threat_rules_cover_each_behaviour_class_and_name_it(parsed):
    threats = [r for r in parsed["rules"] if THREAT_TAG in r["tags"]]
    classes = set()
    for rule in threats:
        behaviour = [t for t in rule["tags"] if t.startswith(BEHAVIOUR_PREFIX)]
        assert len(behaviour) == 1, f"{rule['rule']} needs exactly one {BEHAVIOUR_PREFIX}<class> tag"
        classes.add(behaviour[0][len(BEHAVIOUR_PREFIX):])
    assert classes >= {"network", "filesystem", "process", "credentials", "privilege"}


def test_every_output_lets_the_gateway_match_the_alert_to_one_pod(parsed):
    for rule in parsed["rules"]:
        output = collapse(rule["output"])
        assert "%container.id" in output and "%k8s.pod.name" in output, rule["rule"]


def test_every_rule_is_scoped_to_sandbox_containers_only(parsed):
    for rule in parsed["rules"]:
        full = expand(rule["condition"], parsed["macros"])
        assert "container.id != host" in full, f"{rule['rule']} could match the host"
        assert "k8s.ns.name in (cp_sandbox_namespaces)" in full, f"{rule['rule']} is not namespace-scoped"


def test_threat_rules_exclude_the_container_runtimes_own_setup(parsed):
    for rule in parsed["rules"]:
        if THREAT_TAG in rule["tags"]:
            assert "not proc.name startswith runc" in expand(rule["condition"], parsed["macros"]), rule["rule"]


def test_every_cp_name_used_is_defined(parsed):
    defined = set(parsed["macros"]) | set(parsed["lists"])
    texts = [r["condition"] for r in parsed["rules"]] + list(parsed["macros"].values())
    for text in texts:
        for name in re.findall(r"\bcp_[a-z_]+\b", collapse(text)):
            assert name in defined, f"{name} is used but not defined"


def test_only_known_fields_are_used(parsed):
    texts = [r["condition"] for r in parsed["rules"]] + [r["output"] for r in parsed["rules"]] \
        + list(parsed["macros"].values())
    used = set()
    for text in texts:
        used |= set(re.findall(r"\b(?:evt|proc|fd|container|k8s)\.[a-z_]+(?:\.[a-z_]+)?", collapse(text)))
    assert used <= KNOWN_FIELDS, f"unknown fields (Falco would refuse to load): {sorted(used - KNOWN_FIELDS)}"


def test_only_known_events_are_used(parsed):
    texts = [r["condition"] for r in parsed["rules"]] + list(parsed["macros"].values())
    used = set()
    for text in texts:
        text = collapse(text)
        used |= set(re.findall(r"evt\.type\s*=\s*([a-z0-9_]+)", text))
        for group in re.findall(r"evt\.type\s+in\s+\(([^)]*)\)", text):
            used |= {name.strip() for name in group.split(",") if name.strip() and not name.strip().startswith("cp_")}
    used |= set(parsed["lists"]["cp_privileged_syscalls"])
    assert used <= KNOWN_EVENTS, f"unknown events: {sorted(used - KNOWN_EVENTS)}"


def test_default_deny_policy_lists_are_tight(parsed):
    # Widening these weakens detection; a reviewer should have to touch this test to do it.
    assert parsed["lists"]["cp_sandbox_namespaces"] == ["cloudproject-sandbox"]
    assert set(parsed["lists"]["cp_allowed_programs"]) <= {"python", "python3", "python3.11", "bash", "pwsh"}
    assert set(parsed["lists"]["cp_allowed_write_paths"]) <= {"/dev/null", "/dev/tty"}


# ------------------------------------------------------------------ Helm values and manifests

def load_yaml(name):
    return list(yaml.safe_load_all((FALCO_DIR / name).read_text(encoding="utf-8")))


def test_helm_values_keep_the_settings_the_gateway_depends_on():
    values = load_yaml("values.yaml")[0]
    assert values["driver"]["kind"] == "modern_ebpf"
    # Dropping failed syscalls would hide blocked attempts, the exact behaviour we detect.
    assert values["driver"]["modernEbpf"]["dropFailedExit"] is False
    falco = values["falco"]
    assert falco["rules_files"] == ["/etc/falco/rules.d"], "only our rules should be loaded"
    assert falco["json_output"] is True and falco["json_include_tags_property"] is True
    assert falco["json_include_output_fields_property"] is True
    assert falco["rule_matching"] == "all"
    assert falco["stdout_output"]["enabled"] is True
    assert values["tty"] is True
    assert values["falcoctl"]["artifact"]["follow"]["enabled"] is False


def test_falco_namespace_is_privileged_and_has_an_egress_policy():
    namespace, policy = load_yaml("falco-namespace.yaml")
    assert namespace["metadata"]["name"] == "falco"
    assert namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "privileged"
    assert policy["kind"] == "NetworkPolicy" and policy["metadata"]["namespace"] == "falco"
    assert policy["spec"]["policyTypes"] == ["Egress"]


def test_gateway_gets_read_only_access_to_falco_logs_and_nothing_else():
    role, binding = load_yaml("gateway-falco-rbac.yaml")
    assert role["metadata"]["namespace"] == "falco" == binding["metadata"]["namespace"]
    granted = {(resource, verb) for rule in role["rules"] for resource in rule["resources"] for verb in rule["verbs"]}
    assert granted == {("pods", "get"), ("pods", "list"), ("pods/log", "get")}
    assert binding["subjects"][0]["name"] == "cloudproject-gateway"     # same group as infra/k8s/gateway-rbac.yaml
