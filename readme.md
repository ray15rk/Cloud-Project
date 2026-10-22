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

| Service | Address | Purpose |
| --- | --- | --- |
| Gateway and dashboard | `http://127.0.0.1:8000` | Verify, route, dry run, deliver, and display results |
| Endpoint | `http://127.0.0.1:5000/execute` | Execute the original payload after a successful dry run |
| Central server | `http://127.0.0.1:9000/notify` | Receive and print failure notifications |

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

Run suites sequentially and wait for other payload runs to finish before checking
cleanup:

```powershell
docker ps -a --filter label=cloudproject.sandbox=1
```

No sandbox containers should remain after completed runs. The last recorded full
regression run passed **62 tests**; real browser checks passed on desktop, 390 px,
and 768 px layouts with no uncaught browser errors. See [changelog.md](changelog.md)
for implementation and validation history.

## Remaining AWS work

Follow [AWS Learner Lab setup](docs/aws-setup.md) for credentials and read-only
readiness checks. The Windows helper resolves the standard AWS CLI installation
even when the terminal's PATH is stale. Passing read checks does not establish
permission to create infrastructure.

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

The completed six-probe test verifies local admission and rejection, not cloud
bursting. Python is the current runtime; additional languages are outside the
implemented scope.
