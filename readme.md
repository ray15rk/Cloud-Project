# Secure Dynamic Payload Delivery Pipeline (Relay)

A multi-tier, defense-in-depth distributed framework for safely pushing and executing untrusted mobile code on edge devices. Supports polyglot payloads (**Python**, **Bash / Shell scripts**, and **PowerShell**). Payloads undergo constant-time HMAC-SHA256 verification, EKS dry-run sandboxing, and delivery to target endpoints only after passing all checks.

---

## 1. Prerequisites & Initial Setup (Do This First!)

Before running the web dashboard or testing payloads, complete these one-time setup steps.

> [!IMPORTANT]
> **EKS access and the sandbox image digest must be configured before testing payloads through the gateway.** When EKS is disabled, the gateway fails closed with HTTP 503.

Run all commands from the repository root directory.

### 1.1 Install System Dependencies
* **Python 3.11+**
* **AWS CLI v2 and kubectl** for access to the existing EKS cluster; see the [EKS setup guides](#6-aws-eks-setup-and-execution)
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

### 1.4 Configure EKS

Follow the [macOS/Linux](docs/eks-teammate-setup.md) or
[Windows](docs/eks-teammate-setup-windows.md) guide to set your own AWS profile,
kubeconfig path, ECR image digest, and `EKS_ENABLED=true` in `.env`.

---

## 2. Quickstart: Interactive Web Dashboard

Once EKS access is configured, start the local dashboard:

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

The PowerShell result above applies to the local sandbox path. In cloud-first mode, the current
Falco policy blocks even a harmless PowerShell run because its runtime generates file and network
events. Python and Bash have passed the cloud-first live path; see the [Falco setup guide](docs/falco-setup.md).

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

### 5.1 Pytest test suite

The gateway and EKS queue tests use fakes and do not require AWS. The separate
legacy Docker sandbox tests require Docker Desktop. The Falco client and
Kubernetes log tests start loopback servers, which may be blocked in restricted
environments.

```bash
# Run the complete test suite
python -m pytest src/tests -v -p no:cacheprovider

# Run tests without Docker (skips live Docker dry-run tests)
python -m pytest src/tests -v -m "not docker" -p no:cacheprovider
```

#### Test Suite Breakdown:
* **`test_watchdog.py`**: HMAC-SHA256 integrity, 60s freshness, tamper rejection, and race-condition replay prevention under concurrent thread pools.
* **`test_sandbox.py`**: Enforced zero-network isolation, read-only root filesystems, non-root UID `65534`, CPU timeout termination, and Docker OOM kills.
* **`test_resource_manager.py`**: Atomic slot reservations, memory budget boundaries, and concurrent thread admission contention.
* **`test_gateway.py`**: Gateway verification, fail-closed delivery, and EKS Job integration.
* **`test_serve.py`**: Gateway thread configuration and production dashboard exposure prevention (`DASHBOARD_ENABLED=0`).
* **`test_nginx_conf.py`**: Ingress path whitelisting, non-503 rate-limiting codes, and `proxy_next_upstream off` assertion.
* **`test_cloud_submissions.py`**: SQLite cloud queue lease renewal, inflight capacity enforcement, and crash recovery.
* **`test_dashboard.py`**: Localhost security headers, CSRF page-token verification, and activity telemetry persistence.

Verify that no orphaned sandbox containers remain after running the tests:
```bash
docker ps -a --filter label=cloudproject.sandbox=1
```

---

### 5.2 EKS smoke test

After following the EKS setup guide, submit a clean example:

```bash
python -m src.sender.send src/examples/good.py
```

The sender polls the EKS Job and reports the final result. For Falco canaries,
use `python -m scripts.check_falco` with an authorized cluster identity.

---

### 5.3 NGINX ingress checks

The existing `scripts.check_nginx` benchmark measures the earlier synchronous
local sandbox flow. Its latency and capacity expectations are not valid for
the EKS queue. Use the EKS smoke test above for current end-to-end validation.

---

## 6. AWS EKS setup and execution

The existing Sydney EKS cluster runs disposable dry-run **Kubernetes Jobs**. The
gateway and endpoint still run on your computer. Start with the
[macOS/Linux](docs/eks-teammate-setup.md) or
[Windows](docs/eks-teammate-setup-windows.md) guide for prerequisites, your own
AWS access, `.env` settings, and a smoke test. These guides use the existing
cluster; they do not provision a new one.

Set `EKS_ENABLED=true` after your AWS identity has cluster access and your
public IP can reach its API. Every verified dry run is an EKS Job. Submissions
initially return **HTTP 202** with a `status_url`; the sender polls until the
Job completes. Cluster Autoscaler changes worker node capacity independently.
With `EKS_ENABLED=false`, the gateway rejects dry-run submissions with HTTP 503.

For cloud setup details, see:
* [EKS Team Handoff & Architecture](docs/eks-team-handoff.md)
* [AWS EKS Deployment Notes](infra/eks/README.md)

---

## 7. Behavioural Threat Detection (Falco)

The sandbox's own checks only see the **outcome** of a dry run (timeout, OOM, exit code). A script that tries
the network, catches the error and exits 0 looks harmless. [Falco](https://falco.org) runs on the EKS nodes,
outside the sandbox, and reports what a script actually *attempted* at the syscall level: network sockets,
file writes, other programs, credential paths, privileged syscalls. The gateway rejects the payload
(`HTTP 422`, `threat_detected`) before it is forwarded, and fails closed (`HTTP 503`) if Falco did not
observe the run.

```dotenv
EKS_ENABLED=true
FALCO_ENABLED=true
```

```bash
python -m scripts.check_falco                          # live canary test against your cluster
python -m src.sender.send src/examples/network_swallowed.py   # exits 0 itself, still rejected (422)
```

Install steps, the rule table, tuning, troubleshooting and limits: [Falco setup guide](docs/falco-setup.md).
Rules and policy lists: [`infra/falco/cloudproject_rules.yaml`](infra/falco/cloudproject_rules.yaml).
The cloud-first path currently accepts clean Python and Bash submissions. PowerShell remains blocked
pending a safe way to separate runtime startup events from script behavior. Falco log delivery has also
caused intermittent clean-run HTTP 503 failures during live testing; treat those as infrastructure
failures, not unsafe-script verdicts.
