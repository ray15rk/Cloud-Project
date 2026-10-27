# Secure Dynamic Payload Delivery Pipeline (Relay)

A multi-tier, defense-in-depth distributed framework for safely pushing and executing untrusted mobile code on edge devices. Supports polyglot payloads (**Python**, **Bash / Shell scripts**, and **PowerShell**). Payloads undergo constant-time HMAC-SHA256 verification, local memory admission control, isolated dry-run sandboxing (local Docker or AWS EKS cloud bursting), and are delivered to target endpoints only after passing all checks.

---

## 1. Prerequisites & Initial Setup (Do This First!)

Before running the web dashboard or testing payloads, complete these one-time setup steps.

> [!IMPORTANT]
> **Docker Desktop must be running with the Linux container engine, and the sandbox image must be built before you can test payloads via the website.** If the image is missing or Docker is stopped, the gateway fails closed (`503 Service Unavailable: local sandbox unavailable`) to prevent unverified code execution.

Run all commands from the repository root directory.

### 1.1 Install System Dependencies
* **Python 3.11+**
* **Docker Desktop** (configured with the Linux container engine)
* **NGINX** *(optional for local dashboard, required for production perimeter benchmark)*:
  * **Windows:** Download stable `.zip` from [nginx.org](https://nginx.org) and extract to `C:\nginx`.
  * **macOS:** `brew install nginx`
  * **Linux:** `sudo apt install nginx`

### 1.2 Environment & Dependencies Setup
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

### 1.3 Configure Secrets (`.env`)
Copy the template and generate a cryptographic signing secret:

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

### 1.4 Build the Disposable Sandbox Container Image
Build the local sandbox image that supports Python, Bash, and PowerShell dry-runs:

```bash
docker build -t cloudproject-sandbox:dev docker/sandbox
```

Verify that the image was built and runs as an unprivileged non-root user (`65534:65534`):
```bash
docker image inspect cloudproject-sandbox:dev --format '{{.Config.User}}'
# Expected output: 65534:65534
```

> [!NOTE]
> Dry-runs enforce `--pull=never` to prevent network delays from stalling execution deadlines.

---

## 2. Quickstart: Interactive Web Dashboard

Once Docker Desktop is running and `cloudproject-sandbox:dev` is built, you can test everything using the single-command dashboard!

Ensure ports `5000`, `8000`, and `9000` are free, then run:

**macOS / Linux:**
```bash
.venv/bin/python -m src.dashboard
```

**Windows (PowerShell):**
```powershell
.\.venv\Scripts\python.exe -m src.dashboard
```

Open **`http://127.0.0.1:8000`** in your browser.

> [!TIP]
> This launches all three local core services concurrently in one process:
> * **Gateway (`:8000`)**: Orchestrates HMAC verification, admission control, and dry-run sandboxing.
> * **Endpoint Device (`:5000`)**: Downstream execution host executing only verified scripts.
> * **Central Monitor (`:9000`)**: Collects real-time security alerts and audit telemetry.

### How to Test in the Dashboard:
1. **Load Sample Scripts:** Use the **Load sample** dropdown to select presets across all 3 languages:
   * **Python:** `good.py` (Healthy), `infinite_loop.py` (Timeout), `memory_hog.py` (OOM kill).
   * **Bash:** `good.sh` (Healthy diagnostic), `infinite_loop.sh` (Timeout), `memory_hog.sh` (OOM kill).
   * **PowerShell:** `good.ps1` (Healthy admin task).
   *(Notice that the language badge in the top right automatically updates to **Python 3.11**, **Bash**, or **PowerShell**).*
2. **Upload Your Own Scripts:** Click **Upload script** or drag-and-drop any `.py`, `.sh`, or `.ps1` file into the editor.
3. **Select a Submission Mode:**
   * **Signed payload:** Executes the golden path through all 4 stages.
   * **Tamper test:** Corrupts a single bit in-transit; watch it get rejected at Stage 1 (`HTTP 401 Unauthorized`).
   * **Replay test:** Submits the same nonce twice; 1st succeeds, 2nd is dropped as duplicate (`HTTP 401`).
4. **Click "Run pipeline":** View the live stage timings, sandbox status, and endpoint output console in real-time.

*Press `Ctrl + C` in your terminal to stop all dashboard services.*

---

## 3. Polyglot Architecture & Symmetrical Test Cases

Relay supports heterogeneous edge workloads: **Bash / Shell scripts (`.sh`)** for hardware and OS diagnostics, **Python scripts (`.py`)** for telemetry processing and data analysis, and **PowerShell scripts (`.ps1`)** for administrative tasks.

### 3.1 Supported Language Runtimes (`LANG_CONFIG`)

| Runtime | Key | Extension | Internal Container Command | Edge Fleet Use Case |
| :--- | :--- | :--- | :--- | :--- |
| **Python** | `python` | `.py` | `python3 -I -B -u /sandbox/script.py` | Telemetry processing, data aggregation, automation |
| **Bash / Shell** | `bash` | `.sh` | `bash /sandbox/script.sh` | OS diagnostics, kernel probing, daemon health checks |
| **PowerShell** | `powershell` | `.ps1` | `pwsh -NoProfile -Command ...` | Heterogeneous Windows/Linux administration tasks |

### 3.2 Symmetrical Test Cases (`src/examples/`)

Both Python and Bash have direct, symmetrical test cases:

| Test Scenario | Python Script | Bash Script (`.sh`) | Expected Gateway Response |
| :--- | :--- | :--- | :--- |
| **Golden Path** | `good.py` | `good.sh` | `HTTP 200 OK` (executed on endpoint) |
| **CPU Infinite Loop** | `infinite_loop.py` | `infinite_loop.sh` | `HTTP 422` (`reason: timeout` at 10s deadline) |
| **Memory Exhaustion (OOM)** | `memory_hog.py` | `memory_hog.sh` | `HTTP 422` (`reason: oom_killed` at 128 MB cap) |
| **Network Breach Probe** | — | `network_breach.sh` | Dropped / exit error (`--network none`) |
| **Filesystem Breakout Probe** | — | `filesystem_breakout.sh` | Dropped / read-only error (`--read-only`) |
| **PowerShell Golden Path** | — | `good.ps1` | `HTTP 200 OK` (executed on endpoint) |

### 3.3 Security Boundary & Adversarial Matrix

The disposable sandbox enforces strict multi-layered isolation parameters. The following boundaries have been validated with real adversarial code:

| Injected Adversarial Attack | Runtime Tested | Enforced Sandbox Flag | Observed System Response | Security Result |
| :--- | :--- | :--- | :--- | :--- |
| **Memory Ballooning (OOM)** | Python / Bash | `--memory=128m` | Host kernel OOM killer terminates container; Gateway returns `HTTP 422 oom_killed` | **PASS**: Host RAM protected |
| **CPU Infinite Loop** | Python / Bash | Subprocess `timeout=10` | Container receives `SIGKILL` at 10.0s deadline; Gateway returns `HTTP 422 timeout` | **PASS**: No worker starvation |
| **Outbound Network Breach** | Bash (`curl -I google.com`) | `--network none` | Zero sockets allocated; command fails immediately with exit error | **PASS**: Zero C2 / exfiltration |
| **Filesystem Breakout** | Bash (`echo > /sandbox/hacked.txt`) | `--read-only` | Linux VFS kernel hard-blocks disk write with Read-Only File System error | **PASS**: Host disk immutable |
| **Process Exhaustion (Fork Bomb)** | Python (`multiprocessing`) | `--pids-limit 64` | Linux kernel refuses to allocate 65th process; execution throttled and capped | **PASS**: PID table intact |
| **State Contamination (Write)** | Bash (`echo data > /tmp/ghost.txt`) | `--tmpfs /tmp` | Write isolated inside ephemeral RAM tmpfs of that container invocation | **PASS**: Isolated session |
| **State Contamination (Read)** | Bash (`cat /tmp/ghost.txt`) | `--rm` + tmpfs cleanup | Container is destroyed post-run; 2nd request sees `File not found` | **PASS**: Zero state leakage |

---

## 4. Production NGINX Ingress & CLI Stack

In production mode, client submissions target the NGINX Ingress at **`http://127.0.0.1:8080/submit`**. Port `8000` is kept internal to the gateway.

Ensure your `.env` specifies:
```dotenv
GATEWAY_URL=http://127.0.0.1:8080/submit
```

### 4.1 Manual Multi-Terminal Startup

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
& 'C:\nginx\nginx.exe' -p $nginxPrefix -c nginx.conf -s quit
```

---

### 4.2 Sending Test Payloads via CLI (Terminal 5)

With the production stack running, send payloads through port `8080`:

```bash
# 1. Golden Path (Python): Verified and delivered (HTTP 200)
python -m src.sender.send src/examples/good.py

# 2. Golden Path (Bash): Automatically infers .sh as Bash (HTTP 200)
python -m src.sender.send src/examples/good.sh

# 3. Golden Path (PowerShell): Automatically infers .ps1 as PowerShell (HTTP 200)
python -m src.sender.send src/examples/good.ps1

# 4. Tampered Payload: Dropped at Stage 1 verify (HTTP 401; alerts Terminal 1)
python -m src.sender.send src/examples/good.py --tamper

# 5. Replay Attack: First succeeds, second dropped as duplicate nonce (HTTP 401)
python -m src.sender.send src/examples/good.py --replay

# 6. Infinite Loop: Terminated at 10s deadline (HTTP 422 'timeout')
python -m src.sender.send src/examples/infinite_loop.py
python -m src.sender.send src/examples/infinite_loop.sh

# 7. Memory Exhaustion: Terminated at 128 MB cap (HTTP 422 'oom_killed')
python -m src.sender.send src/examples/memory_hog.py
python -m src.sender.send src/examples/memory_hog.sh
```

---

## 5. Automated Testing & Verification

### 5.1 Pytest Test Suite (92 Tests)
Ensure Docker Desktop is running before executing:

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

### 5.2 Multi-Language HTTP & Adversarial Boundary Suite
Execute the multi-language validation test covering standard execution and adversarial boundary probing across Python, Bash, and PowerShell:

```bash
python run_all.py
```
Or run the test script directly against an active Gateway on `:8000`:
```bash
python src/tests/test_gateway_http.py
```

---

### 5.3 Live NGINX Ingress & Benchmark Checks (12 Checks)
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
12. Measures median (~580 ms) and p95 (~650 ms) round-trip execution latency.

---

## 6. AWS EKS Cloud Bursting (Hybrid Architecture)

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
   * Worker node capacity is managed dynamically in-cluster via **Kubernetes Cluster Autoscaler**.

For cloud setup details, see:
* [EKS Team Handoff & Architecture](docs/eks-team-handoff.md)
* [AWS EKS Deployment Notes](infra/eks/README.md)
* [Windows EKS Setup Guide](docs/eks-teammate-setup-windows.md)
* [macOS / Linux EKS Setup Guide](docs/eks-teammate-setup.md)
