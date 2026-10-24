import pytest
from src.sandbox.sandbox import dry_run

# Mark tests with @pytest.mark.docker to prevent output of confusing erorrs when docker is not running
# Register marker to avoid warnings about unknown markers
pytestmark = pytest.mark.docker

def test_normal_scripts():
    # Baseline case to test that a normal script runs successfully in the sandbox
    result = dry_run(b"print('Hello from the sandbox')")
    assert result["status"] == "OK"
    assert "Hello from the sandbox" in result["stdout"]

def test_memory_limit_exceeded():
    # Test that a script that keeps allocating memory should hit memory limit and get killed by OOM killer
    script = b"""data = []
while True:
    data.append(" " * 10**6)  # Keep allocating memory
"""
    result = dry_run(script, memory_limit="50m")  # Set a low memory limit for testing
    print(result["detail"])
    assert result["status"] == "oom_killed"

def test_infinite_loop_timeout():
    # A CPU bound infinite loop never allocates enough memory to trigger OOM kill
    # CPU throttles it but it will never finish, so we need to use a timeout to terminate it
    script = b"""while True:
    pass  # Infinite loop
"""
    result = dry_run(script, timeout_seconds=2)  # Set a short timeout for testing
    assert result["status"] == "timeout"

def test_timeout_leaves_no_container():
    # After a timeout, the infinite loop container must actually be gone, not just abandoned
    import subprocess, time
    dry_run(b"while True:\n    pass\n", timeout_seconds=2)
    time.sleep(1)  # Give Docker a moment to finish removing the killed container
    running = subprocess.run(
        ["docker", "ps", "-q", "--filter", "label=cloudproject.sandbox=1"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert running == "", f"Leftover sandbox containers: {running}"

def test_no_network_access():
    # A payload trying to reach the internet during its dry run must fail
    script = b"""import socket
socket.create_connection(("1.1.1.1", 53), timeout=3)
"""
    result = dry_run(script)
    assert result["status"] == "error"

def test_runs_as_non_root():
    # The sandbox must not run as root (uid 0)
    result = dry_run(b"import os; print(os.getuid())")
    assert result["status"] == "OK"
    assert result["stdout"].strip() != "0"


def test_utf8_output_is_preserved():
    text = "Hello, caf\u00e9 \u4f60\u597d \U0001f680"
    result = dry_run(f"print({text!r})".encode("utf-8"))
    assert result["status"] == "OK"
    assert result["stdout"] == text + "\n"

def test_cannot_write_filesystem():
    # The container filesystem is read-only, so writing a file must fail
    result = dry_run(b"open('/tmp/evil.txt', 'w').write('x')")
    assert result["status"] == "error"


def test_container_output_is_bounded():
    result = dry_run(b"print('x' * 100000)")
    assert result["status"] == "OK"
    assert len(result["stdout"].encode("utf-8")) == 64 * 1024
