from types import SimpleNamespace

from src.gateway.node_scaler import NodeScaler


def pod(phase, reason=None, message=None):
    conditions = [] if reason is None else [SimpleNamespace(
        type="PodScheduled", status="False", reason=reason, message=message)]
    return SimpleNamespace(status=SimpleNamespace(phase=phase, conditions=conditions))


def test_scales_up_only_for_resource_pending_pod(monkeypatch):
    scaler = NodeScaler(idle_seconds=300)
    group = {"status": "ACTIVE", "scalingConfig": {"minSize": 1, "maxSize": 2, "desiredSize": 1}}
    changes = []
    monkeypatch.setattr(scaler, "_nodegroup", lambda: group)
    monkeypatch.setattr(scaler, "_set_desired", changes.append)
    monkeypatch.setattr(scaler, "_pods", lambda: [pod("Pending", "Unschedulable", "node selector mismatch")])
    scaler.step(now=0)
    assert changes == []
    monkeypatch.setattr(scaler, "_pods", lambda: [pod("Pending", "Unschedulable", "Insufficient cpu")])
    scaler.step(now=20)
    assert changes == [2]


def test_scales_down_only_after_idle_cooldown(monkeypatch):
    scaler = NodeScaler(idle_seconds=300)
    group = {"status": "ACTIVE", "scalingConfig": {"minSize": 1, "maxSize": 2, "desiredSize": 2}}
    changes = []
    pods = [pod("Running")]
    monkeypatch.setattr(scaler, "_nodegroup", lambda: group)
    monkeypatch.setattr(scaler, "_set_desired", changes.append)
    monkeypatch.setattr(scaler, "_pods", lambda: pods)
    scaler.step(now=0)
    pods.clear()
    scaler.step(now=1)
    scaler.step(now=299)
    assert changes == []
    scaler.step(now=301)
    assert changes == [1]


def test_refuses_unexpected_nodegroup_bounds(monkeypatch):
    scaler = NodeScaler()
    monkeypatch.setattr(scaler, "_pods", lambda: [pod("Pending", "Unschedulable", "Insufficient memory")])
    monkeypatch.setattr(scaler, "_nodegroup", lambda: {"status": "ACTIVE", "scalingConfig": {
        "minSize": 1, "maxSize": 5, "desiredSize": 1}})
    try:
        scaler.step(now=0)
    except RuntimeError as error:
        assert "minSize=1 and maxSize=2" in str(error)
    else:
        assert False, "Unexpected bounds must fail closed"
