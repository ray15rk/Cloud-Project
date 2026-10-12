# Cloud Computing Project

## SETUP
1. Install Python 3.11+ and Docker Desktop.
 
2. From the Cloud-Project folder, create and activate a virtual environment:
    python -m venv .venv
    .venv\Scripts\Activate.ps1          (Windows)
    source .venv/bin/activate           (macOS / Linux)
 
3. pip install -r requirements.txt
 
4. Create a .env where .env.example is, copy contents, and set a real secret:
    python -c "import secrets; print(secrets.token_hex(32))"
    Paste the result as SHARED_SECRET. .env is gitignored - never commit it.
 
5. Pre-pull the sandbox image, otherwise the first dry run is counted as a timeout:
    docker pull python:3.11-slim
 
All commands must be run from the Cloud-Project folder.
All of this project's own modules (src.*) must be run with `python -m`, not a direct
script path (e.g. `python -m src.gateway.gateway_app`, not `python src/gateway/gateway_app.py`) -
otherwise absolute imports like `from src.sandbox.sandbox import dry_run` fail with
`ModuleNotFoundError`.

## TESTS
All tests (Docker Desktop must be running):   python -m pytest src/tests -v
Without Docker:                               python -m pytest src/tests -v -m "not docker"
 
 
After the run, confirm no sandbox containers leaked: docker ps --filter label=cloudproject.sandbox=1

### Endpoint test (debugging only)
Tests the endpoint on its own, bypassing the gateway. 
Useful for checking whether a failure in the full demo comes from the endpoint or from earlier in the pipeline.

**WARNING**: this skips the HMAC check and the sandbox. The endpoint runs whatever it is sent, so only send code you wrote yourself. 
This is not how payloads normally reach it.

Terminal 1:  python -m src.endpoint.endpoint_app

Terminal 2 (PowerShell):
  @"
import requests
response = requests.post(
     'http://127.0.0.1:5000/execute',
     data=b'print("hello from the endpoint")',
     headers={'Content-Type': 'application/octet-stream'},
 )
print(response.json())
"@ | python

## RUNNING THE FULL PIPELINE

This runs all three real processes together over actual HTTP - not mocks, not isolated
unit tests. Needs four terminals, all opened from the Cloud-Project folder, with the
venv activated and .env already set up (Setup step 4).

Terminal 1 - central server (failure notification inbox):
    python -m src.sender.central_server

Terminal 2 - endpoint (Device B):
    python -m src.endpoint.endpoint_app

Terminal 3 - gateway (watchdog -> dry run -> forward):
    python -m src.gateway.gateway_app

Terminal 4 - send test payloads through the gateway:

    Golden path (should deliver, HTTP 200):
        python -m src.sender.send src/examples/good.py

    Tampered payload (should be dropped at "verify", HTTP 401,
    and Terminal 1 should print the failure notification):
        python -m src.sender.send src/examples/good.py --tamper

    Replayed payload (first attempt 200, second attempt dropped as replay, HTTP 401):
        python -m src.sender.send src/examples/good.py --replay

    CPU-bound infinite loop (should be dropped at "dry_run", timeout, HTTP 422):
        python -m src.sender.send src/examples/infinite_loop.py

    Memory leak (should be dropped at "dry_run", oom_killed, HTTP 422):
        python -m src.sender.send src/examples/memory_hog.py

Each send.py call prints the HTTP status and the gateway's JSON response - check both
the status code AND the "stage"/"reason" fields match what's expected above, not just
that it didn't crash.

## NOTES
- EKS cloud-burst path — resource manager currently fails closed (drops the
  payload, HTTP 503 stage "capacity") rather than bursting, since no cluster
  exists yet. Needs, in order: (1) EKS + ECR provisioning and moving the
  sandbox's hardening flags into a Job spec, (2) replacing the fail-closed
  branch in gateway_app.py with real Job submission + retry/backoff for IAM
  propagation delay (Section 5.3), (3) re-running the "3 scripts, 2 fit
  locally" scenario against a real cluster, (4) deciding whether
  ResourceManager needs EKS-side capacity awareness too, not just local.