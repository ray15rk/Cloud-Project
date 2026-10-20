# Takes a verified payload and execute in a resource limited docker container
# Container is used to catch malicious or runaway runtime behavior, not to validate the output

# ASSUMPTION FOR NOW: Payload is a python script, need to handle more languagues later

import os
import subprocess
import tempfile
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

def prepare_script_file(payload: bytes) -> Path:
    # Create a temporary file to hold the payload script
    # Docker run moiunts files and no in memory python bytes, so we need to write the payload to a temporary file first
    # Write to "/temp" to avoid permission issues
    # "delete = False" is used to keep the file around after closing it, so that Docker can access it
    tmp = tempfile.NamedTemporaryFile(delete=False, mode='wb', suffix='.py')
    tmp.write(payload)
    tmp.close()
    os.chmod(tmp.name, 0o644)  # Readable by the unprivileged sandbox user, writable only by owner
    return Path(tmp.name)

def sandbox_execute(script_path: Path, cpu_limit: str = "0.5", memory_limit: str = "128m", timeout_seconds: int = 10) -> dict:
    # Run script inside disposable docker container with resource limits and report what happened 
    # Docker's --cpus flag takes a plain number of cores, while Kubernetes uses a millicore format
    # Need to know the conversion so it is consistent for YAML files later

    # Unique name per run so we can find and kill this exact container if it times out
    container_name = f"sandbox-{uuid.uuid4().hex[:12]}"

    docker_cmd = [
        "docker", 
        "run", 
        "--pull=never",  # Build the image before serving requests; do not pull within the deadline
        "--rm", # Remove container when it exists else other dry run leaves a dead container 
        "--name", container_name,
        "--label", "cloudproject.sandbox=1",  # Lets tests and cleanup scripts find every sandbox container
        f"--memory={memory_limit}", 
        f"--cpus={cpu_limit}", 

        # Hardening, matches Section 5.2 of the proposal
        "--network", "none",                       # No network access during the dry run
        "--user", "65534:65534",                   # Run as unprivileged "nobody", not root
        "--read-only",                             # Container filesystem cannot be modified
        "--cap-drop", "ALL",                       # Remove all special Linux privileges
        "--security-opt", "no-new-privileges",     # Block regaining privileges via setuid tricks
        "--pids-limit", "64",                      # Cap process count, stops fork bombs

        # Mount script into the container with read only
        # Sandbox can process read but never modify it to prevent script tempering mid run
        "-v", f"{script_path}:/sandbox/script.py:ro", 

        # The same runtime image can be tagged for ECR and used by EKS Jobs.
        # Its entrypoint runs the per-request script mounted above.
        os.environ.get("SANDBOX_IMAGE", "cloudproject-sandbox:dev"),
        ]

    try:
        # Timeout used to terminate a CPU throttled intinite loop script, otherwise it will run forever and never return
        result = subprocess.run(docker_cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        # The timeout only killed the docker CLI client, the container is still running inside the Docker engine
        # Tell the engine to kill it; --rm then deletes it automatically
        subprocess.run(["docker", "kill", container_name], capture_output=True)
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
