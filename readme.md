# Secure Dynamic Payload Delivery Pipeline

A multi-tier, defense-in-depth pipeline for safely pushing and executing untrusted mobile code on edge devices. Payloads undergo HMAC-SHA256 verification, local memory admission control, isolated dry-run sandboxing (local Docker or AWS EKS cloud bursting), and are delivered to target endpoints only after passing all checks.

---

## Quickstart: Web Dashboard

Start **Docker Desktop** (Linux containers). Ensure ports `5000`, `8000`, and `9000` are free, then run the single-command launcher:

**macOS / Linux:**
```bash
.venv/bin/python -m src.dashboard
```

**Windows:**
```powershell
.\.venv\Scripts\python.exe -m src.dashboard
```

Open **`http://127.0.0.1:8000`** in your browser.

> [!NOTE]
> This starts all three local core services concurrently (Gateway on `:8000`, Endpoint on `:5000`, and Central Monitor on `:9000`). It provides a browser workbench with Python editing, preset samples (`good`, `infinite_loop`, `memory_hog`), tamper/replay modes, live timing metrics, live capacity probes, and SQLite activity history.

---

## Prerequisites & Initial Setup

Run all commands from the repository root directory.

### 1. Install System Dependencies
* **Python 3.11+**
* **Docker Desktop** (configured with the Linux container engine)
* **NGINX**:
  * **Windows:** Download the stable `.zip` from [nginx.org](https://nginx.org) and extract to a path without spaces (e.g., `C:\nginx`).
  * **macOS:** `brew install nginx`
  * **Linux:** `sudo apt install nginx`

### 2. Environment & Dependencies Setup
Create and activate a virtual environment:

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate

# Windows (PowerShell)
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install application dependencies:
```bash
pip install -r requirements.txt
```

### 3. Configure Secrets (`.env`)
Copy the template and generate a cryptographic secret:

```bash
# macOS / Linux
cp env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"

# Windows (PowerShell)
Copy-Item env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```
Paste the generated output as `SHARED_SECRET` inside `.env`.  
*(Never commit `.env` to version control).*

### 4. Build the Sandbox Container Image
```bash
docker build -t cloudproject-sandbox:dev docker/sandbox
```
Dry-runs enforce `--pull=never` to prevent image downloads from delaying payload execution deadlines. Verify the image user is non-root (`65534:65534`):
```bash
docker image inspect cloudproject-sandbox:dev --format '{{.Config.User}}'
```

> [!IMPORTANT]
> Always execute project modules using `python -m src.<module>` rather than running script files directly to ensure absolute package imports resolve correctly.

---

## NGINX Ingress & Production Stack

In production mode, client submissions target the NGINX Ingress at **`http://127.0.0.1:8080/submit`**. Port `8000` is kept internal.

Ensure your `.env` specifies:
```dotenv
GATEWAY_URL=http://127.0.0.1:8080/submit
```

### Manual Multi-Terminal Startup

Activate the virtual environment in each terminal, then run:

| Terminal | Service | Command | Description |
| :--- | :--- | :--- | :--- |
| **1** | Central Monitor | `python -m src.sender.central_server` | Listens for failure reports on `:9000` |
| **2** | Endpoint Device | `python -m src.endpoint.endpoint_app` | Downstream execution host on `:5000` |
| **3** | Production Gateway | `python -m src.gateway.serve` | Waitress WSGI (1 process, 8 threads, 4 slots) on `:8000` |
| **4** | NGINX Proxy | *(See commands below)* | Perimeter rate/conn proxy on `:8080` |

#### Starting NGINX:
**macOS / Linux:**
```bash
nginx -t -p "$PWD/nginx/" -c nginx.conf
nginx -p "$PWD/nginx/" -c nginx.conf

# To stop:
nginx -p "$PWD/nginx/" -c nginx.conf -s quit
```

**Windows (PowerShell):**
```powershell
$nginxPrefix = (Join-Path (Get-Location).Path 'nginx').Replace('\', '/') + '/'
& 'C:\nginx\nginx.exe' -t -p $nginxPrefix -c nginx.conf
& 'C:\nginx\nginx.exe' -p $nginxPrefix -c nginx.conf

# To stop:
$nginxPrefix = (Join-Path (Get-Location).Path 'nginx').Replace('\', '/') + '/'
>> & 'C:\nginx\nginx.exe' -p $nginxPrefix -c nginx.conf -s quit
```

---

## Sending Test Payloads (CLI)

With the pipeline running, send payloads through port `8080`:

```bash
# 1. Golden Path: Verified and executed on endpoint (HTTP 200)
python -m src.sender.send src/examples/good.py

# 2. Tampered Payload: Dropped at 'verify' stage (HTTP 401; alerts Terminal 1)
python -m src.sender.send src/examples/good.py --tamper

# 3. Replay Attack: First succeeds, second dropped as duplicate nonce (HTTP 401)
python -m src.sender.send src/examples/good.py --replay

# 4. Infinite Loop: Terminated at 10s deadline (HTTP 422 'timeout')
python -m src.sender.send src/examples/infinite_loop.py

# 5. Memory Exhaustion: Terminated at 128 MB cap (HTTP 422 'oom_killed')
python -m src.sender.send src/examples/memory_hog.py
```

---

## Automated Testing & Verification

The project includes unit, integration, and live stress-test suites covering cryptographic security, container isolation, memory budget admission, NGINX ingress rules, and cloud bursting.

### 1. Pytest Test Suite (92 Tests)
Ensure Docker Desktop is running with the Linux container engine before executing:

```bash
# Run the complete test suite (all 92 unit, integration, and security tests)
python -m pytest src/tests -v -p no:cacheprovider

# Run tests without Docker (skips live Docker dry-run tests)
python -m pytest src/tests -v -m "not docker" -p no:cacheprovider
```

#### Test Suite Breakdown:
* **`test_watchdog.py`**: HMAC-SHA256 integrity, 60s freshness, tamper rejection, and race-condition replay prevention under concurrent thread pools.
* **`test_sandbox.py`**: Enforced zero-network isolation, read-only root filesystems, non-root UID `65534`, CPU timeout termination, and Docker OOM kills.
* **`test_resource_manager.py`**: Atomic slot reservations, memory budget boundaries, and concurrent thread admission contention.
* **`test_gateway.py`**: Full 5-stage pipeline routing, fail-closed omission failure semantics, and EKS cloud overflow integration.
* **`test_serve.py`**: Thread-to-slot ratio validation and production dashboard exposure prevention (`DASHBOARD_ENABLED=0`).
* **`test_nginx_conf.py`**: Ingress path whitelisting, non-503 rate-limiting codes, and `proxy_next_upstream off` assertion.
* **`test_cloud_submissions.py`**: SQLite cloud queue lease renewal, inflight capacity enforcement, and crash recovery.
* **`test_node_scaler.py`**: Dynamic 1-to-2 worker node group autoscaling logic for resource-pending Pods.
* **`test_dashboard.py`**: Localhost security headers, CSRF page-token verification, and activity telemetry persistence.

Verify that no orphaned sandbox containers remain after running the tests:
```bash
docker ps -a --filter label=cloudproject.sandbox=1
```

---

### 2. Live NGINX Ingress & Benchmark Checks (12 Checks)
With the stack running (Central Monitor, Endpoint, Gateway, and NGINX active), run the automated ingress integration benchmark:

```bash
python -m scripts.check_nginx --benchmark --report artifacts/nginx-manual.json
```

This verifies **12 end-to-end security and load conditions**:
1. Internal dashboard routes return `404` from outside the perimeter.
2. `GET /submit` is rejected with `403` (POST-only route).
3. Legitimate signed scripts pass and execute (`HTTP 200`).
4. Tampered payloads are rejected at Stage 1 (`HTTP 401`).
5. Replayed payloads are rejected on duplicate submission (`HTTP 401`).
6. Payloads exceeding 256 KB are rejected directly at NGINX (`HTTP 413`).
7. 6 simultaneous slow scripts: 4 admitted locally, 2 rejected at `stage: capacity` (`HTTP 503`).
8. 9th concurrent client rejected by NGINX connection ceiling (`HTTP 429`).
9. Stalled Slowloris clients are dropped within 5.0s to protect backend worker threads.
10. 60-request flood is bounded and absorbed by NGINX (`HTTP 429` / `400`).
11. 40 req/s sustained traffic burst is throttled according to rate limits.
12. Measures median (~530 ms) and p95 (~580 ms) round-trip execution latency.

---

### 3. Optional End-to-End Browser Tests (Playwright)
Validates the web dashboard UI, script uploads, drag-and-drop, and execution traces across desktop and mobile viewport sizes:

```bash
pip install -r requirements-dev.txt
playwright install chromium
python -m scripts.check_dashboard
```
Artifact screenshots are generated in `artifacts/`.

---

### 4. Isolated Endpoint Testing (Debugging Only)

> [!CAUTION]
> Testing the endpoint directly **bypasses HMAC verification and sandbox execution**. Only submit code you author yourself.

```bash
# Terminal 1: Run endpoint
python -m src.endpoint.endpoint_app

# Terminal 2: POST directly
python -c "import requests; print(requests.post('http://127.0.0.1:5000/execute', data=b'print(\"hello direct\")').json())"
```

---

## AWS EKS Cloud Bursting (Hybrid Architecture)

When local memory capacity (default: 2 slots / 256 MB) is exceeded, the gateway overflows dry-runs to disposable **Kubernetes Jobs** in AWS EKS.

1. **Prerequisites:**
   * Active AWS Academy Learner Lab credentials saved in `~/.aws/credentials`.
   * Current public IP added to the EKS cluster API allowlist.
2. **Configuration (`.env`):**
   ```dotenv
   EKS_ENABLED=true
   EKS_CLUSTER=cloudproject-sandbox-lab
   EKS_CONTEXT=cloudproject-sandbox-lab
   EKS_NODEGROUP=sandbox-workers
   EKS_MAX_INFLIGHT=3
   LOCAL_MEMORY_BUDGET_MB=256
   ```
3. **Execution Behavior:**
   * Synchronous local requests finish immediately.
   * Overflowed requests return **`HTTP 202 Accepted`** with a `status_url`.
   * Clients poll `/submissions/<id>` until the EKS Job completes and the verified payload is forwarded to the local endpoint.
   * A gateway `NodeScaler` monitors resource-pending Pods and dynamically adjusts the EKS managed node group between 1 and 2 nodes.

For cloud setup details, see:
* [EKS Team Handoff & Architecture](docs/eks-team-handoff.md)
* [AWS EKS Deployment Notes](infra/eks/README.md)
* [Windows EKS Setup Guide](docs/eks-teammate-setup-windows.md)
* [macOS / Linux EKS Setup Guide](docs/eks-teammate-setup.md)
