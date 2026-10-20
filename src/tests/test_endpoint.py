import subprocess
from pathlib import Path

import pytest
import src.endpoint.endpoint_app as endpoint


def test_endpoint_preserves_utf8_output():
    text = "Hello, caf\u00e9 \u4f60\u597d \U0001f680"
    response = endpoint.app.test_client().post("/execute", data=f"print({text!r})".encode("utf-8"))
    assert response.status_code == 200
    assert response.json["returncode"] == 0
    assert response.json["stdout"] == text + "\n"


@pytest.mark.parametrize("failure,status", [(subprocess.TimeoutExpired("python", 10), 504), (OSError("runtime unavailable"), 503)])
def test_endpoint_failures_clean_up_the_script(monkeypatch, failure, status):
    paths = []
    def fail(command, **kwargs):
        paths.append(Path(command[-1]))
        assert paths[-1].exists()
        raise failure
    monkeypatch.setattr(endpoint.subprocess, "run", fail)
    response = endpoint.app.test_client().post("/execute", data=b"print('hello')")
    assert response.status_code == status
    assert response.json["executed"] is False
    assert paths and all(not path.exists() for path in paths)
