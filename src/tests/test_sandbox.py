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