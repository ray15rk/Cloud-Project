# Cloud Computing Project

## DASHBOARD

Start Docker Desktop. Stop any old gateway, endpoint, and central server processes
with Ctrl+C, then run this single command from the project directory:

On macOS:

```sh
.venv/bin/python -m src.dashboard
```

On Windows:

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
execution. Cloud overflow is available when `EKS_ENABLED=true` and the EKS
settings below are configured.

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

2. Install NGINX. On Windows, download the stable zip from **nginx.org** and unzip it to a path without spaces, such as C:\nginx. On macOS or Linux, use brew install nginx or apt install nginx.
    
 
3. From the Cloud-Project folder, create and activate a virtual environment:
    python -m venv .venv
    .venv\Scripts\Activate.ps1          (Windows)
    source .venv/bin/activate           (macOS / Linux)
 
4. pip install -r requirements.txt
 
5. Copy env.example to .env and set a real secret:
    Copy-Item env.example .env          (PowerShell; only if .env does not exist)
    python -c "import secrets; print(secrets.token_hex(32))"
    Paste the result as SHARED_SECRET. .env is gitignored - never commit it.
 
6. Start Docker Desktop with the Linux container engine, then build the sandbox image:
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
as UID/GID 65534. Its built-in runner executes /sandbox/script.py with a
10-second script deadline and a 64 KiB combined output cap. The gateway
mounts each verified payload there read-only; payloads and shared secrets are
not included in the image. The build context is limited to docker/sandbox.

The local runner still applies memory and CPU limits, network isolation, a
read-only filesystem, dropped capabilities, no-new-privileges, a PID limit,
and a 10-second outer execution deadline. The in-container runner adds its own
deadline for EKS Jobs, where the local Docker client timeout is unavailable.

After building, check the image and then run the existing tests:

```powershell
docker image inspect cloudproject-sandbox:dev --format '{{.Config.User}}'
python -m pytest src/tests -v
docker ps -a --filter label=cloudproject.sandbox=1
```

The image user should be 65534:65534, tests should pass, and no sandbox containers
should remain. If python opens the Microsoft Store, use py to create the virtual
environment, then use .\.venv\Scripts\python.exe for subsequent Python commands.

The EKS image has been published to ECR, and the gateway can run overflow dry
runs as hardened Kubernetes Jobs. See [EKS team handoff](docs/eks-team-handoff.md)
for the architecture and options for teammates, and
[deployment notes](infra/eks/README.md) for this Learner Lab environment.

## Nginx
You need Docker Desktop running and the sandbox image built (docker build -t cloudproject-sandbox:dev docker/sandbox).

This is to set up and test Nginx. Make sure Nginx is already downloaded from the above SETUP steps.

Run in five terminals (venv active, from the project folder). For the original
Nginx checks, start the gateway with `EKS_ENABLED=false`; those checks expect
overflow to return HTTP 503. To test EKS overflow through Nginx, leave
`EKS_ENABLED=true` and use the sender CLI instead.

1. Terminal 1: `python -m src.sender.central_server`
2. Terminal 2: `python -m src.endpoint.endpoint_app`
3. Terminal 3: `python -m src.gateway.serve` (should print Gateway on http://127.0.0.1:8000 (8 threads, 4 sandbox slots, dashboard off)) 
4. Terminal 4: Start NGINX (change file path accordingly). Windows: `FILE_PATH_TO\nginx.exe -p  YOUR_FILE_PATH_TO/Cloud-Project/nginx/" -c nginx.conf` or macOS/Linux: `nginx -p "$PWD/nginx/" -c nginx.conf`
5. Terminal 5: `python -m scripts.check_nginx`. You should see 9 [PASS] lines and 9/9 checks passed. The headline one is “6 simultaneous slow payloads: 4 delivered, 2 rejected at stage ‘capacity’”.

To send through Nginx, set `GATEWAY_URL=http://127.0.0.1:8080/submit` in
`.env`, then run `python -m src.sender.send src/examples/good.py`. Nginx
proxies both `POST /submit` and the read-only `/submissions/<id>` status URL,
so the CLI can poll EKS results without exposing the dashboard.

To stop Nginx, simply run `FILE_PATH_TO\nginx.exe -p  YOUR_FILE_PATH_TO/Cloud-Project/nginx/" -c nginx.conf -s quit` for Windows or `nginx -p "$PWD/nginx/" -c nginx.conf -s quit` for macOS / Linux

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

## EKS overflow on macOS

See [the live EKS deployment guide](infra/eks/README.md) for the cluster and
image identifiers. In your gitignored `.env`, set `LOCAL_MEMORY_BUDGET_MB=256`,
`EKS_ENABLED=true`, `EKS_CONTEXT=cloudproject-sandbox-lab`, and `EKS_IMAGE` to
the immutable ECR image URI shown there. Run the dashboard with
`.venv/bin/python -m src.dashboard`. The AWS CLI credentials in
`~/.aws/credentials` must still be valid, and the cluster API allowlist must
include your current public IP.

Local submissions finish synchronously. Verified overflow submissions return
HTTP 202 with a `submission_id` and `status_url`. The sender CLI and dashboard
poll the status URL until the EKS dry run and endpoint delivery finish. The
gateway keeps at most three outstanding cloud submissions by default; set
`EKS_MAX_INFLIGHT` to change that bound. Pending payloads are stored temporarily
in a gitignored SQLite file with owner-only permissions and removed at terminal
status. A failed or uncertain endpoint forwarding attempt is never retried
automatically.

The endpoint executes the forwarded script directly on its host. Only the
dry run is isolated in EKS. Node autoscaling is currently off by operator
choice; the managed node group stays at one node until manually resized.

This is a hybrid local/cloud demonstration. The gateway, dashboard, central
server, and final endpoint run on the Mac at `127.0.0.1`; they are not hosted
in EKS. Teammates can run the local-only mode from a clone. Using this EKS
cluster from another machine requires separate AWS/Kubernetes authorization
and network access; a teammate with a different Learner Lab account needs
their own cluster and ECR image.
