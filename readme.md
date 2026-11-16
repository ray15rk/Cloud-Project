# Secure Dynamic Payload Delivery Pipeline (Relay)

A multi-tier, defense-in-depth distributed framework for safely pushing and executing untrusted mobile code on edge devices. Supports polyglot payloads (**Python**, **Bash / Shell scripts**, and **PowerShell**). Payloads undergo constant-time HMAC-SHA256 verification, EKS dry-run sandboxing, and delivery to target endpoints only after passing all checks.

**New teammate?** You do not need to create an AWS account. You can install
the project and run the [automated tests](#51-pytest-test-suite) on your device
without AWS. To submit a real dry run or demo the dashboard, the project
owner must first [invite you to the AWS project](docs/aws-setup.md). AWS grants
invited teammates broad access to that project's resources. There is no local Docker fallback
for gateway dry runs.

---

## 1. Prerequisites & Initial Setup (Do This First!)

Before submitting payloads through the dashboard, complete these one-time
setup steps. The automated tests in section 5.1 can run without AWS access.

> [!IMPORTANT]
> **EKS access and the sandbox image digest must be configured before testing payloads through the gateway.** When EKS is disabled, the gateway fails closed with HTTP 503.

Run all commands from the repository root directory.

### 1.1 Install System Dependencies
* **Python 3.11+**
* **AWS CLI v2.32.0 or newer and kubectl** for live access to the existing EKS cluster; see the [EKS setup guides](#6-aws-eks-setup-and-execution)
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
test -f .env || cp env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"

# Windows (PowerShell)
if (-not (Test-Path .env)) { Copy-Item env.example .env }
python -c "import secrets; print(secrets.token_hex(32))"
```
Paste the generated output as `SHARED_SECRET` inside `.env`.  
*(Never commit `.env` to version control).*

### 1.4 Configure EKS

First, have the owner [invite you to the project](docs/aws-setup.md#project-owner-invite-a-teammate)
and accept the invitation using your own AWS Builder ID.
The sandbox image digest is in the [EKS deployment notes](infra/eks/README.md#sandbox-image).
Follow the [macOS/Linux](docs/eks-teammate-setup.md) or
[Windows](docs/eks-teammate-setup-windows.md) guide to set your own AWS profile,
kubeconfig path, ECR image digest, `EKS_ENABLED=true`, and `FALCO_ENABLED=true` in `.env`.
Each person generates a kubeconfig on their device and sets `KUBECONFIG` to
that device's absolute path. Create `.env` from `env.example` locally and set
`AWS_PROFILE` to the profile used for `aws login`.
Use `GATEWAY_URL=http://127.0.0.1:8000/submit` for the dashboard demo; use
port `8080` only when NGINX is running. The endpoint also needs the selected
language runtime locally: Python, Bash (Git for Windows supplies it), or PowerShell.
PowerShell currently triggers Falco startup findings and is not a clean demo case.

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
   * **PowerShell:** `good.ps1` (currently blocked by Falco startup findings).
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
| **PowerShell** | `powershell` | `.ps1` | `pwsh -NoProfile -File /sandbox/script.ps1` | Heterogeneous Windows/Linux administration tasks |

### 3.2 Symmetrical Test Cases (`src/examples/`)

Accepted submissions first return HTTP 202. The sender polls the status URL.
The completed status endpoint returns HTTP 200 with the final outcome in its
`status_code` field (200, 422, or 503); the CLI prints that outcome as “final”.

| Scenario | Example | Expected final outcome |
| --- | --- | --- |
| Clean Python or Bash | `good.py`, `good.sh` | 200, delivered after a clean Falco verdict |
| Infinite loop | `infinite_loop.py`, `infinite_loop.sh` | 422, timeout or a Falco finding |
| Memory exhaustion | `memory_hog.py`, `memory_hog.sh` | 422, memory failure or a Falco finding |
| Network attempt that catches its error | `network_swallowed.py` | 422, Falco network finding, no delivery |
| File write attempt | `fs_write_swallowed.py` | 422, Falco filesystem finding, no delivery |
| PowerShell startup | `good.ps1` | Currently blocked by runtime-generated Falco findings |

These are expected outcomes; see [recorded verification and limits](docs/project-review-2026-11-12.md).
A monitoring failure can instead produce 503. Falco findings take precedence over
script exit status. Upload the Python canaries through the dashboard or use the CLI.

### 3.3 EKS isolation and limits

The [Job template](infra/k8s/sandbox-job.yaml.template) specifies 128 MiB memory,
500 mCPU, UID 65534, a read-only root filesystem, no privilege escalation,
dropped capabilities, RuntimeDefault seccomp, no service-account token, and a
16 MiB memory-backed `/tmp`. The runner imposes a 10-second script deadline;
the Job has a separate 300-second overall deadline. Namespace network policy
blocks sandbox network traffic when enforced by the cluster CNI.

The old Docker helper's `--pids-limit 64` is not an EKS Job setting. Do not claim
an equivalent EKS PID limit without checking the worker configuration. Falco
observes a run and can block later delivery; it does not stop a running script.
A clean dry run does not prove safe behavior on a different host. The local
endpoint executes in its host runtime and has no gateway authentication of its
own, so keep it on loopback. This is a demonstration, not a production deployment.

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
| **3** | Production Gateway | `python -m src.gateway.serve` | Waitress WSGI (1 process, 8 HTTP threads; default 2 EKS workers, 3 outstanding submissions) on `:8000` |
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

# 3. PowerShell: currently blocked by Falco runtime findings
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
# Complete suite: first start Docker and build the legacy test image
docker build -t cloudproject-sandbox:dev docker/sandbox
python -m pytest src/tests -v -p no:cacheprovider

# Run tests without Docker (skips live Docker dry-run tests)
python -m pytest src/tests -v -m "not docker" -p no:cacheprovider
```

The current suite contains 160 collected tests. With Docker running, all 160
should pass. Without Docker, run the
`not docker` command above; it runs 151 tests and skips nine legacy Docker-marked tests. The Falco
client and Kubernetes log tests use local test servers, not a live EKS cluster.
For the last live EKS check, see the [review record](docs/project-review-2026-11-12.md).

---

### 5.2 EKS smoke test

After following the EKS setup guide, submit a clean example:

```bash
python -m src.sender.send src/examples/good.py
```

The sender polls the gateway status URL and reports the final result. For Falco canaries,
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

For cloud setup details, see [AWS EKS Deployment Notes](infra/eks/README.md).

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
The EKS path currently accepts clean Python and Bash submissions. PowerShell remains blocked
pending a safe way to separate runtime startup events from script behavior. Falco log delivery has also
caused intermittent clean-run HTTP 503 failures during live testing; treat those as infrastructure
failures, not unsafe-script verdicts.

---

## 8. Known Limitations

| Area | Limitation & Workaround |
| :--- | :--- |
| **PowerShell in Cloud Mode** | Blocked under EKS by Falco DaemonSet because `pwsh` engine startup generates file and network syscall events that trigger benign security alerts. Python and Bash run cleanly. `EKS_ENABLED=false` rejects dry-run submissions. |
| **Intermittent Cloud HTTP 503** | Falco log retrieval over Kubernetes API can occasionally experience timing latency where the gateway does not observe `CP Sandbox Observed` within the settle deadline, failing closed with HTTP 503. This is an infrastructure polling latency, not a malicious code detection. Retry the submission. |
| **Local Sandbox Unit Tests** | 6 tests in `src/tests/test_sandbox.py` require a running local Docker Desktop with Linux containers. When Docker is not active, these 6 tests fail with pipe connection errors. Run the `not docker` test command above when Docker is stopped. |
| **Live Falco Client Tests** | `test_falco_real_client.py` and `test_kube_logs.py` use local loopback test servers; they do not require EKS credentials. |

---

## 9. Project Directory Structure

```
relay/
├── docker/sandbox/       # Container Dockerfile & entrypoint runner for polyglot sandboxing
├── docs/                 # Operational guides: AWS EKS, Windows/Linux teammate setup, Falco setup, NGINX
├── infra/
│   ├── eks/              # IAM & node autoscaler policies, cluster launch configuration
│   ├── falco/            # DaemonSet values, Kubernetes RBAC, and cloudproject_rules.yaml
│   └── k8s/              # Disposable sandbox Job template, namespace quota, NetworkPolicies
├── nginx/                # Ingress reverse proxy configuration (rate limiting, timeouts, body cap)
├── scripts/              # Health-check diagnostic scripts (check_stack, check_falco, check_nginx, etc.)
├── src/
│   ├── dashboard.py      # Integrated 3-in-1 server runner (Gateway + Endpoint + Monitor)
│   ├── ingress.py        # System-wide configuration timeouts and constants
│   ├── endpoint/         # Mock edge endpoint application (:5000/execute)
│   ├── examples/         # Symmetrical sample scripts (Python, Bash, PowerShell; healthy & adversarial)
│   ├── gateway/          # Flask gateway, HMAC verification, SQLite cloud queue, and live UI
│   ├── resource_manager/ # Atomic slot reservation & local concurrency controller
│   ├── sandbox/          # EKS Job runner, Falco syscall log monitor, and local Docker sandbox
│   ├── sender/           # Signed payload generator CLI and centralized monitor inbox (:9000)
│   ├── tests/            # Pytest suite (unit, integration, adversarial)
│   └── watchdog/         # Canonical HMAC-SHA256 signing, verification, and TTL NonceStore
├── env.example           # Environment variable template with placeholders
├── pytest.ini            # Test runner configuration
├── readme.md             # Authoritative project documentation & execution guide
├── requirements.txt      # Production runtime dependencies
└── run_all.py            # Automated gateway boot and test suite execution script
```

## Demo and verification records

See the [demo runbook](docs/demo-runbook.md) for a recording sequence and
[review record](docs/project-review-2026-11-12.md) for the latest checks and remaining gaps.
