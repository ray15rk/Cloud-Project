# Cloud Computing Project

## DASHBOARD

Start Docker Desktop. Stop any old gateway, endpoint, and central server processes
with Ctrl+C, then run this single command from the project directory:

```powershell
.\.venv\Scripts\python.exe -m src.dashboard
```

Open **http://127.0.0.1:8000**. This starts all three local services together.
The dashboard supports Python uploads and editing, sample scripts, signed,
tampered, and replayed submissions, execution traces, service health, concurrent
capacity probes, activity search/filtering, JSON details, and CSV export.

Run `docker build -t cloudproject-sandbox:dev docker/sandbox` first if the image
has not been built. Follow SETUP below if dependencies or `.env` are missing.
Ctrl+C stops the dashboard's three services. The original four-terminal workflow
and sender CLI still work; gateway submissions also appear in dashboard history.

The operator dashboard is restricted to localhost. Signing happens on the server;
the shared HMAC secret is never sent to the browser. Only run scripts you trust,
since a successful dry run forwards the original script for real endpoint
execution. EKS is visibly disconnected; no cloud execution is simulated.

Activity metadata and bounded endpoint output are saved in
`.data/activity.sqlite3` (gitignored). The latest 100 records are displayed and
exportable; up to 2,000 records are retained. Editor drafts stay in this browser's
local storage. Scripts and HMAC keys are not stored in the activity database.

### Dashboard verification

```powershell
.\.venv\Scripts\python.exe -m pytest src/tests -v -p no:cacheprovider
```

For the optional real browser + Docker checks (temporary database, separate ports):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m scripts.check_dashboard
```

Screenshots are saved in `artifacts/` (gitignored).

## SETUP
1. Install Python 3.11+ and Docker Desktop.
 
2. From the Cloud-Project folder, create and activate a virtual environment:
    python -m venv .venv
    .venv\Scripts\Activate.ps1          (Windows)
    source .venv/bin/activate           (macOS / Linux)
 
3. pip install -r requirements.txt
 
4. Copy env.example to .env and set a real secret:
    Copy-Item env.example .env          (PowerShell; only if .env does not exist)
    python -c "import secrets; print(secrets.token_hex(32))"
    Paste the result as SHARED_SECRET. .env is gitignored - never commit it.
 
5. Start Docker Desktop with the Linux container engine, then build the sandbox image:
    docker build -t cloudproject-sandbox:dev docker/sandbox

   Build before starting the gateway. Dry runs use --pull=never so downloading an
   image cannot consume a payload's execution deadline. Rebuild after changing
   the Dockerfile. SANDBOX_IMAGE in .env selects the image; the default is
   cloudproject-sandbox:dev.
 
All commands must be run from the Cloud-Project folder.
All of this project's own modules (src.*) must be run with `python -m`, not a direct
script path (e.g. `python -m src.gateway.gateway_app`, not `python src/gateway/gateway_app.py`) -
otherwise absolute imports like `from src.sandbox.sandbox import dry_run` fail with
`ModuleNotFoundError`.

## SANDBOX IMAGE

The image in docker/sandbox/Dockerfile contains the Python 3.11 runtime and runs
as UID/GID 65534. Its default command executes /sandbox/script.py. The gateway
mounts each verified payload there read-only; payloads and shared secrets are
not included in the image. The build context is limited to docker/sandbox.

The local runner still applies memory and CPU limits, network isolation, a
read-only filesystem, dropped capabilities, no-new-privileges, a PID limit,
and a 10-second execution deadline. These are runtime settings, not protections
that a Dockerfile alone can enforce.

After building, check the image and then run the existing tests:

```powershell
docker image inspect cloudproject-sandbox:dev --format '{{.Config.User}}'
python -m pytest src/tests -v
docker ps -a --filter label=cloudproject.sandbox=1
```

The image user should be 65534:65534, tests should pass, and no sandbox containers
should remain. If python opens the Microsoft Store, use py to create the virtual
environment, then use .\.venv\Scripts\python.exe for subsequent Python commands.

For the next milestone, tag and push this same image to ECR, then use that ECR
image URI in a hardened Kubernetes Job. The Job must reproduce the runtime
protections, disable payload retries, and clean up after completion. EKS routing
and IAM retry handling remain separate implementation work; building this image
does not enable cloud bursting.

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
- For the next project phase, follow [AWS Learner Lab setup](docs/aws-setup.md).
- EKS cloud-burst path — resource manager currently fails closed (drops the
  payload, HTTP 503 stage "capacity") rather than bursting, since no cluster
  exists yet. Needs, in order: (1) EKS + ECR provisioning and moving the
  sandbox's hardening flags into a Job spec, (2) replacing the fail-closed
  branch in gateway_app.py with real Job submission + retry/backoff for IAM
  propagation delay (Section 5.3), (3) re-running the "3 scripts, 2 fit
  locally" scenario against a real cluster, (4) deciding whether
  ResourceManager needs EKS-side capacity awareness too, not just local.
