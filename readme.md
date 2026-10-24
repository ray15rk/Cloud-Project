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
Ctrl+C stops the dashboard's three services. Use the Nginx workflow below for
the sender CLI; gateway submissions also appear in dashboard history.

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
Teammates with separate accounts can follow the
[macOS/Linux EKS setup](docs/eks-teammate-setup.md) or
[Windows PowerShell EKS setup](docs/eks-teammate-setup-windows.md).

## Nginx

The public submission path is `http://127.0.0.1:8080/submit`. Port 8000 is
internal to the gateway. In an existing `.env`, replace the old
`GATEWAY_URL=http://127.0.0.1:8000/submit` with:

```dotenv
GATEWAY_URL=http://127.0.0.1:8080/submit
```

Do not put `set` or `export` inside `.env`. The sender default and env.example
already use 8080. Docker Desktop must be running with Linux containers, and the
sandbox image must exist:

```sh
docker info --format '{{.OSType}}'
docker build -t cloudproject-sandbox:dev docker/sandbox
```

The Nginx capacity checks expect `EKS_ENABLED=false`, so overflow returns
HTTP 503. To demonstrate EKS cloud bursting through Nginx, use
`EKS_ENABLED=true` and send signed scripts with `src.sender.send`. Nginx also
proxies `/submissions/<id>`, which the sender polls for cloud results.

Both `nginx/logs/.gitkeep` and `nginx/temp/.gitkeep` are tracked. If a downloaded
copy omits empty directories, create `nginx/logs` and `nginx/temp` before starting.
There is no pip package that supplies these project directories.

### Complete automated verification

With the venv active, from the project root:

```sh
python -m scripts.check_stack
```

This runs the full test suite, starts the central server, endpoint, gateway and
project Nginx, checks ingress protections, measures normal traffic, exercises the
sender examples, deliberately stops the endpoint and gateway to test outages,
checks the real 45-second proxy timeout and aggregated notifications, and stops its own services. It records step
results, measurements and service logs in a printed `artifacts/nginx-*` folder.
It refuses to start if ports 5000, 8000, 8080 or 9000 are already occupied and
never stops a pre-existing service. A successful run exits with code zero.

On macOS, Homebrew may already run its default Nginx welcome-page server on 8080.
Check with `brew services list` and `lsof -nP -iTCP:8080 -sTCP:LISTEN`. If that is
the conflicting server, stop it using `brew services stop nginx` before testing;
restore it afterward using `brew services start nginx` if it was previously on.
On Windows, stop the existing Nginx instance using its original prefix/config;
do not terminate unrelated Python processes. If the command is unavailable, add
the Nginx installation directory to PATH before using the automated runner.

### Manual startup (five terminals, plus optional monitoring)

Activate the venv in each terminal, from the project root. Use
`source .venv/bin/activate` on macOS/Linux or `.\.venv\Scripts\Activate.ps1` in
PowerShell. Then:

1. Central server: `python -m src.sender.central_server`
2. Endpoint: `python -m src.endpoint.endpoint_app`
3. Gateway: `python -m src.gateway.serve` (one process, eight threads, four sandbox slots)
4. Validate and start the project Nginx using the commands below.
5. Verify: `python -m scripts.check_nginx --benchmark --report artifacts/nginx-manual.json`

macOS/Linux (keep the quoted prefix because the project path may contain spaces):

```sh
nginx -t -p "$PWD/nginx/" -c nginx.conf
nginx -p "$PWD/nginx/" -c nginx.conf
# Shutdown after testing:
nginx -p "$PWD/nginx/" -c nginx.conf -s quit
```

Windows PowerShell (adjust the executable path):

```powershell
$nginxPrefix = (Join-Path (Get-Location).Path 'nginx').Replace('\', '/') + '/'
& 'C:\nginx\nginx.exe' -t -p $nginxPrefix -c nginx.conf
& 'C:\nginx\nginx.exe' -p $nginxPrefix -c nginx.conf
# Shutdown after testing:
& 'C:\nginx\nginx.exe' -p $nginxPrefix -c nginx.conf -s quit
```

The live check prints a result for each protection and fails on missing request
outcomes or thread errors. Expect four delivered and two capacity-rejected
submissions in the six-request probe. With `--benchmark` it also checks ten
normal signed submissions, twenty paced ingress requests and eighty sustained-overload
requests. Proxy overload is
429; local sandbox capacity is 503, stage `capacity`.

For continuous ingress alerts, run a sixth terminal:

```sh
python -m scripts.monitor_nginx
```

This sends at most one aggregated notification every ten seconds when new
408/413/429 or proxy-generated 502/504 failures occur. It stays quiet otherwise.
`NOTIFY_URL` must point to the central server. Only counts and up to three request
IDs are sent, never payloads or secrets. The cursor is saved in
`.data/nginx-monitor.json` after acknowledgement; failed delivery is retried.
Run one monitor per cursor file. Lost acknowledgements can produce duplicate
summaries. Rename rotated logs and run `nginx -p ... -c nginx.conf -s reopen`;
avoid rotation before the monitor has consumed the current file's tail.

Use Ctrl+C to stop the monitor and Python services; stop Nginx with `-s quit`.
The automated runner performs these shutdown steps for its own services.

### Limits, timeouts, and evaluation

See [Nginx evaluation](docs/nginx-evaluation.md) for measured results and tuning.
The default limit is 20 requests/s per IP, burst 10, plus eight active requests
per IP and eight in total. Keep the Nginx concurrency cap at or below the gateway
thread count when tuning. The six-request capacity probe deliberately fits below
that cap. These are local-demo settings, not a claim of production capacity.

Nginx buffers request bodies before forwarding. Client header/body inactivity is
limited to five seconds; gateway connect/send/read inactivity limits are 2/5/45
seconds. The sender uses a 3-second connect timeout and 60-second read timeout;
endpoint forwarding uses 2/15 seconds and the sandbox execution deadline is ten
seconds. This leaves processing margin without a client timing out before the
proxy's configured read timeout. These are layered inactivity limits, not a hard
end-to-end cancellation guarantee: slow trickles and work already dispatched may
outlive a disconnected client. Do not automatically retry a script whose outcome
is unknown. Review the Learner Lab budget before running EKS workloads.

JSON access logs include request ID, status, upstream timings and rejection
source; bodies and query strings are excluded. Nginx returns JSON for its own
413/429/502/504 errors and preserves gateway JSON errors. The sender also handles
HTML errors from other proxies. Keep the single-process gateway: multiple Python
workers would have separate replay stores and capacity counters until shared
state is implemented.

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
unit tests. Use the five-terminal Nginx startup above, with the venv activated and `.env`
configured. Then send the following examples through port 8080.

Terminal 1 - central server (failure notification inbox):
    python -m src.sender.central_server

Terminal 2 - endpoint (Device B):
    python -m src.endpoint.endpoint_app

Terminal 3 - gateway (watchdog -> dry run -> forward):
    python -m src.gateway.serve

Terminal 5 - send test payloads through Nginx (start Nginx in Terminal 4 as above):

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
dry run is isolated in EKS. In the Learner Lab, an optional gateway-side node
scaler watches sandbox Pods for resource scheduling shortages and adjusts the
managed node group between one and two nodes. It requires the local gateway
and current AWS lab credentials to keep running; it is not Kubernetes Cluster
Autoscaler. See [EKS deployment](infra/eks/README.md).

This is a hybrid local/cloud demonstration. The gateway, dashboard, central
server, and final endpoint run on the Mac at `127.0.0.1`; they are not hosted
in EKS. Teammates can run the local-only mode from a clone. Using this EKS
cluster from another machine requires separate AWS/Kubernetes authorization
and network access; a teammate with a different Learner Lab account needs
their own cluster and ECR image.
