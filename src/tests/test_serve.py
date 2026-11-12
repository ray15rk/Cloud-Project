import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.gateway.serve import choose_threads  # serve.py has no import-time side effects

ROOT = Path(__file__).resolve().parents[2]


def gateway_routes(dashboard_flag):
    # A fresh interpreter per case, so the environment flag cannot leak into other tests
    env = {k: v for k, v in os.environ.items() if k != "DASHBOARD_ENABLED"}
    if dashboard_flag is not None:
        env["DASHBOARD_ENABLED"] = dashboard_flag
    code = "from src.gateway.gateway_app import app; print(sorted(r.rule for r in app.url_map.iter_rules()))"
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, env=env, check=True).stdout


def test_default_threads():
    assert choose_threads(None) == 8


def test_explicit_threads_are_allowed():
    assert choose_threads("6") == 6


@pytest.mark.parametrize("value", ["0", "-1"])
def test_nonpositive_threads_are_rejected(value):
    with pytest.raises(SystemExit, match="at least 1"):
        choose_threads(value)


def test_dashboard_is_on_by_default():
    routes = gateway_routes(None)
    assert "/api/run" in routes and "/submit" in routes


def test_dashboard_routes_do_not_exist_when_disabled():
    routes = gateway_routes("0")
    assert "/submit" in routes
    assert "/api/run" not in routes and "/api/overview" not in routes and "'/'" not in routes
