# Takes a verified payload and execute in a resource limited docker container
# Container is used to catch malicious or runaway runtime behavior, not to validate the output

# ASSUMPTION FOR NOW: Payload is a python script, need to handle more languagues later

import subprocess
import tempfile
from pathlib import Path

def prepare_script_file(payload: bytes) -> Path:
    # Create a temporary file to hold the payload script
    # Docker run moiunts files and no in memory python bytes, so we need to write the payload to a temporary file first
    # Write to "/temp" to avoid permission issues
    # "delete = False" is used to keep the file around after closing it, so that Docker can access it
    tmp = tempfile.NamedTemporaryFile(delete=False, mode='wb', suffix='.py')
    tmp.write(payload)
    tmp.close()
    return Path(tmp.name)

def sandbox_execute(script_path: Path, cpu_limit: str = "0.5", memory_limit: str = "128m", timeout_seconds: int = 10) -> dict:
    # Run script inside disposable docker container with resource limits and report what happened 
    # Docker's --cpus flag takes a plain number of cores, while Kubernetes uses a millicore format
    # Need to know the conversion so it is consistent for YAML files later

    docker_cmd = [
        "docker", 
        "run", 
        "--rm", # Remove container when it exists else other dry run leaves a dead container 
        f"--memory={memory_limit}", 
        f"--cpus={cpu_limit}", 

        # Mount script into the container with read only
        # Sandbox can process read but never modify it to prevent script tempering mid run
        "-v", f"{script_path}:/sandbox/script.py:ro", 

        # Need to build a custom harded image later, now just use the official python image and run the script directly
        "python:3.11-slim", 
        "python", "/sandbox/script.py"
        ]

    try:
        # Timeout used to terminate a CPU throttled intinite loop script, otherwise it will run forever and never return
        result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "detail": f"Script did not finish within {timeout_seconds}s, likely due to CPU bound infinite loop"
        }

    # Docker exit code "137" is used to indicate that the container was killed due to exceeding memory limits
    # Detect the OOM kill case without neeeding to parse any log output
    if result.returncode == 137:
        return {
            "status": "oom_killed",
            "detail": f"Script was killed due to exceeding memory limit of {memory_limit}"
        }

    # Using the return code to determine if the script ran successfully or failed, and returning the appropriate status and details
    if result.returncode != 0:
        return {
            "status": "error",
            "detail": result.stderr.strip(),
            "returncode": result.returncode
        }

    return {
        "status": "OK",
        "stdout": result.stdout
    }

def dry_run(payload: bytes, cpu_limit: str = "0.5", memory_limit: str = "128m", timeout_seconds: int = 10) -> dict:
    # Write payload to temp file, run it in sandbox, then clean up temp file regardless of result
    script_path = prepare_script_file(payload)
    try:
        return sandbox_execute(script_path, cpu_limit, memory_limit, timeout_seconds)
    finally:
        # Clean up the temporary script file after execution to avoid leaving behind unnecessary files
        script_path.unlink()