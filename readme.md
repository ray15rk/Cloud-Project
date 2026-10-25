# Cloud Computing Project — Relay

Relay delivers trusted Python scripts through local integrity verification, memory
admission, a resource-bounded Docker dry run, and real endpoint execution.

```text
Signed payload → HMAC / replay check → memory admission → Docker dry run → endpoint
                         Rejection or failure → central server notification
```

The local pipeline and dashboard are implemented. **EKS cloud bursting is pending**:
when local capacity is exhausted, the gateway returns HTTP 503 at stage `capacity`.
It does not execute the payload in AWS.

## First-time setup

Run commands from the project root. Windows examples use the virtual environment's
Python directly, so activation is optional.

1. Install Python 3.11+ and Docker Desktop. Start Docker Desktop with the **Linux
   container engine**.
2. Create the environment and install application dependencies:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. Copy `env.example` to `.env` **only if `.env` does not already exist**. Generate
   a secret and replace the `SHARED_SECRET` placeholder in `.env` with its output:

   ```powershell
   .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
   ```

   Preserve existing configuration. The sender and gateway must use the same
   secret. `.env` is gitignored; never commit its contents.
4. Build the project sandbox image before starting the gateway:

   ```powershell
   docker build -t cloudproject-sandbox:dev docker/sandbox
   ```

   Dry runs use `--pull=never`: a missing image causes a failure rather than a
   download during execution. Rebuild after changing the Dockerfile.

On macOS/Linux, use `python3 -m venv .venv` and replace the Windows executable in
subsequent commands with `.venv/bin/python`. If Windows `python` opens the Microsoft
Store, use `py` to create the environment and the explicit executable afterward.
Run project modules with `-m`, not their direct file paths, to preserve imports.

## Start the dashboard

Stop separately running gateway, endpoint, and central server processes with
**Ctrl+C**, then run:

```powershell
.\.venv\Scripts\python.exe -m src.dashboard
```

Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)** and leave the terminal running.
The launcher starts all three services on localhost:

Run `docker build -t cloudproject-sandbox:dev docker/sandbox` first if the image
has not been built. Follow SETUP below if dependencies or `.env` are missing.
Ctrl+C stops the dashboard's three services. Use the Nginx workflow below for
the sender CLI; gateway submissions also appear in dashboard history.

Ctrl+C stops the launcher's services. If a port is occupied, stop the old processes
and retry. Restart after changing Python code or `.env`, then refresh the browser.

### Features

- **Overview:** submission/delivery/failure counts, average processing time, and
  current local memory reservations.
- **Workbench:** Python editing, `.py` upload and drag-and-drop, built-in good,
  infinite-loop, and memory-exhaustion samples, plus signed/tamper/replay modes.
  Scripts must be valid UTF-8 and at most 64 KB. Ctrl+Enter submits from the editor.
- **Execution trace:** actual stage outcomes, available timings, HTTP result codes,
  and output or failure reasons. Replay mode displays both attempts.
- **Activity:** search, filters, JSON details, clipboard actions, recent failure
  summaries, and CSV export of the filtered records.
- **System status:** gateway, Docker, sandbox image, endpoint, and central server
  health. Manual refresh bypasses the health cache; EKS is marked disconnected.
- **Capacity test:** concurrent built-in probes. With the default 512 MB budget and
  128 MB reservations, six probes produce four local admissions and two capacity
  rejections when no other submissions are running.

The operator dashboard is restricted to localhost and requires a page token for
submission actions. Signing happens on the server; the HMAC secret is not sent to
the browser. The gateway never forwards a payload that fails verification or its
dry run.

The dry run checks bounded runtime behavior, not functional correctness. Run only
trusted scripts: successful payloads execute again on the real endpoint, which
has a timeout but no Docker CPU/memory isolation.

### Saved data

Activity metadata and endpoint output are saved in `.data/activity.sqlite3`.
Up to 2,000 records are retained; the latest 100 are displayed, searched, and
exportable. CSV export follows the active filter within that set. Counts and
averages cover all retained records. Stored endpoint stdout and stderr are each
truncated after 16,000 characters when necessary.

Submitted source, HMAC tags, and keys are not saved as journal fields; content
printed by a script can still appear in its recorded output. Editor drafts are
saved separately in browser local storage. Central notifications are best effort
and printed to its terminal; the dashboard's failure list comes from gateway
history, not a separate central inbox database.

`.env`, `.venv/`, `.data/`, and generated `artifacts/` are gitignored. Recreate local
configuration, dependencies, and images when setting up another machine.

## Configuration and limits

Defaults are in [env.example](env.example). Restart services after editing `.env`.

| Variable | Default / requirement |
| --- | --- |
| `SHARED_SECRET` | Required: replace the placeholder with a generated secret |
| `SANDBOX_IMAGE` | `cloudproject-sandbox:dev` |
| `ENDPOINT_URL` | `http://127.0.0.1:5000/execute` |
| `GATEWAY_URL` | `http://127.0.0.1:8000/submit`, used by the sender CLI |
| `NOTIFY_URL` | `http://127.0.0.1:9000/notify` in the example; if unset, failures are printed locally |

The launcher binds the three default ports. Use the individual service workflow
below when configuring a different endpoint or central server.

The image in [docker/sandbox/Dockerfile](docker/sandbox/Dockerfile) is based on
`python:3.11-slim-bookworm`, runs as UID/GID `65534:65534`, and executes
`/sandbox/script.py`. Payloads are mounted read-only per run, rather than included
in the image. Its build context is limited to `docker/sandbox`.

| Runtime setting | Current default |
| --- | --- |
| Local dry-run memory budget | 512 MB, reserved atomically within the gateway process |
| Reservation and container memory limit per dry run | `128m` |
| Container CPU limit | `0.5` cores |
| Local dry-run deadline | 10 seconds |
| Sandbox process limit | 64 |
| Request body / decoded script limit | 256 KB / 64 KB |
| HMAC freshness and nonce retention | 60 seconds; nonce state is in memory |
| Endpoint execution timeout | 10 seconds |
| Gateway endpoint HTTP timeout | 15 seconds |
| Failure notification HTTP timeout | 2 seconds |

Docker runtime flags enforce no network, a read-only filesystem, a non-root user,
dropped capabilities, and no-new-privileges. The Dockerfile alone does not enforce
these restrictions. Local memory accounting tracks this process's reservations,
not all host memory or multiple gateway workers. CPU/memory limits are currently
code defaults, not `.env` options.

## Original CLI and individual services

The sender CLI works with either the launcher or separately started services.
**Choose one service-start method** to avoid port conflicts. For the original
four-terminal workflow, run these in terminals 1–3 respectively:

```powershell
.\.venv\Scripts\python.exe -m src.sender.central_server
```

```powershell
.\.venv\Scripts\python.exe -m src.endpoint.endpoint_app
```

```powershell
.\.venv\Scripts\python.exe -m src.gateway.gateway_app
```

The gateway also serves the dashboard in this mode. In terminal 4, send one test
at a time:

```powershell
.\.venv\Scripts\python.exe -m src.sender.send src/examples/good.py
.\.venv\Scripts\python.exe -m src.sender.send src/examples/good.py --tamper
.\.venv\Scripts\python.exe -m src.sender.send src/examples/good.py --replay
.\.venv\Scripts\python.exe -m src.sender.send src/examples/infinite_loop.py
.\.venv\Scripts\python.exe -m src.sender.send src/examples/memory_hog.py
```

| Scenario | Expected gateway result |
| --- | --- |
| Valid script | HTTP 200, `delivered: true`, endpoint return code 0 |
| Tampering | HTTP 401, stage `verify`, HMAC failure |
| Replay | First 200, then 401 at `verify` |
| Infinite loop | HTTP 422, stage `dry_run`, reason contains `timeout` |
| Memory exhaustion | HTTP 422, stage `dry_run`, reason contains `oom_killed` |
| Local budget exhausted | HTTP 503, stage `capacity` |
| Sandbox unavailable | HTTP 503, stage `dry_run` |
| Endpoint delivery or execution failure | HTTP 502, stage `forward` |

Check HTTP status and JSON stage/reason. Pipeline failures trigger a central
notification attempt and enter activity history. For dashboard `/api/run` and
`/api/capacity-test`, outer HTTP 200 means the test request completed; each
attempt's `status_code` contains its actual pipeline outcome.

Gateway `/submit` accepts a base64 payload, nonce, timestamp, and HMAC tag; the CLI
builds this message. Direct endpoint `/execute` calls bypass verification and
dry-run checks and are for trusted debugging only. Endpoint and central server
`GET /health` routes support dashboard checks.

## Verification

Run the full suite with Docker Desktop running and the sandbox image built:

```powershell
.\.venv\Scripts\python.exe -m pytest src/tests -v -p no:cacheprovider
```

Without Docker:

```powershell
.\.venv\Scripts\python.exe -m pytest src/tests -v -m "not docker" -p no:cacheprovider
```

Optional real browser checks:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m scripts.check_dashboard
```

The browser script uses temporary history and separate ports. It checks the real
CLI, HTTP services, and Docker execution, plus upload/drag-and-drop, Unicode,
rejected files, traces, capacity, notifications, search/filters, CSV export,
clipboard, details, history reload, and responsive navigation. Service
unavailability is tested with controlled failures. Screenshots are saved to
`artifacts/relay-desktop.png` and `artifacts/relay-mobile.png`.

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
as UID/GID 65534. Its default command executes /sandbox/script.py. The gateway
mounts each verified payload there read-only; payloads and shared secrets are
not included in the image. The build context is limited to docker/sandbox.

The local runner still applies memory and CPU limits, network isolation, a
read-only filesystem, dropped capabilities, no-new-privileges, a PID limit,
and a 10-second execution deadline. These are runtime settings, not protections
that a Dockerfile alone can enforce.

After building, check the image and then run the existing tests:

```powershell
docker ps -a --filter label=cloudproject.sandbox=1
```

No sandbox containers should remain after completed runs. The last recorded full
regression run passed **62 tests**; real browser checks passed on desktop, 390 px,
and 768 px layouts with no uncaught browser errors. See [changelog.md](changelog.md)
for implementation and validation history.

## Remaining AWS work

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
is unknown. Revisit the budget before adding real EKS execution.

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

1. Publish the built image to ECR; provision EKS with suitable roles, bounded worker
   capacity, and node autoscaling.
2. Provision/verify the Auto Scaling service-linked role during deployment and add
   bounded infrastructure-readiness / IAM-propagation retries.
3. Implement disposable Kubernetes Jobs with equivalent resource/security controls,
   isolated networking, disabled payload retries, result collection, and cleanup.
4. Connect gateway overflow to EKS while retaining local verification, notifications,
   and forwarding only after a successful dry run.
5. Demonstrate three concurrent submissions with two local slots and one real EKS
   run; collect cloud-transition, failure, and overload metrics for the report.

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
