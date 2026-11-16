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

### 1.4 Configure EKS & AWS Access

Relay runs disposable dry-run sandboxes on an existing AWS EKS cluster (`cloudproject-sandbox-sydney` in `ap-southeast-2`, account `562358727339`). Teammates do not need static IAM credentials; access is granted via AWS Project sharing and AWS Builder ID.

#### Step 1: Accept the Project Invitation
1. Have the project owner invite your email via AWS Settings projects.
2. Accept the invitation at [settings.aws.com/projects](https://settings.aws.com/projects) using your **AWS Builder ID**.
3. Confirm you see the shared project (e.g., **Stack of Dreams**) in your projects list.

#### Step 2: Sign in and Generate Kubeconfig

**Windows (PowerShell):**
```powershell
$Profile = 'cloudproject-sydney'
aws login --profile $Profile
# Browser prompt: authenticate with AWS Builder ID, select region 'ap-southeast-2' and the invited project.

$Kubeconfig = Join-Path $env:USERPROFILE '.kube\config-cloudproject-sydney'
New-Item -ItemType Directory -Force (Split-Path $Kubeconfig) | Out-Null
aws eks update-kubeconfig --profile $Profile --region ap-southeast-2 --name cloudproject-sandbox-sydney --alias cloudproject-sandbox-sydney --kubeconfig $Kubeconfig
$env:KUBECONFIG = $Kubeconfig

# Verify cluster connectivity:
kubectl --context cloudproject-sandbox-sydney -n cloudproject-sandbox get pods
```

*(Expected output: `No resources found in cloudproject-sandbox namespace.`)*

> [!IMPORTANT]
> **Cluster API IP Whitelist (If `kubectl` hangs with `i/o timeout`):**  
> For defense-in-depth, the EKS cluster restricts public control-plane API access to authorized teammate CIDRs. If you are on a new machine, new Wi-Fi, or mobile hotspot, check your current public IP:
> ```powershell
> (Invoke-WebRequest -Uri "https://checkip.amazonaws.com").Content.Trim()
> ```
> If `kubectl` times out connecting to port 443, dynamically retrieve the existing allowed CIDRs and append your current IP without hardcoding personal addresses:
> ```powershell
> $myIp = (Invoke-WebRequest -Uri "https://checkip.amazonaws.com").Content.Trim()
> $current = (aws eks describe-cluster --name cloudproject-sandbox-sydney --profile cloudproject-sydney --query "cluster.resourcesVpcConfig.publicAccessCidrs" --output text) -split "`t"
> $updated = ($current + "$myIp/32" | Select-Object -Unique) -join ","
> aws eks update-cluster-config --name cloudproject-sandbox-sydney --resources-vpc-config publicAccessCidrs="$updated" --profile cloudproject-sydney
> ```
> *(Optional: To remove your IP when testing is finished: `$reverted = ($current | Where-Object { $_ -ne "$myIp/32" }) -join ","; aws eks update-cluster-config --name cloudproject-sandbox-sydney --resources-vpc-config publicAccessCidrs="$reverted" --profile cloudproject-sydney`)*
>
> Wait for the update to complete (`aws eks describe-update ...`), then retry `kubectl`.

**macOS / Linux (Bash / Zsh):**
```bash
export AWS_PROFILE=cloudproject-sydney
aws login --profile "$AWS_PROFILE"
# Browser prompt: authenticate with AWS Builder ID, select region 'ap-southeast-2' and the invited project.

export KUBECONFIG="$HOME/.kube/config-cloudproject-sydney"
mkdir -p "$(dirname "$KUBECONFIG")"
aws eks update-kubeconfig --profile "$AWS_PROFILE" --region ap-southeast-2 --name cloudproject-sandbox-sydney --alias cloudproject-sandbox-sydney --kubeconfig "$KUBECONFIG"

# Verify cluster connectivity:
kubectl --context cloudproject-sandbox-sydney -n cloudproject-sandbox get pods
```
*(Expected output: `No resources found in cloudproject-sandbox namespace.`)*

#### Step 3: Populate `.env`
Ensure your local `.env` has the verified settings pointing to your generated kubeconfig and cluster:

```dotenv
# Core pipeline configuration
SHARED_SECRET=<your_generated_32_byte_hex_secret>
GATEWAY_URL=http://127.0.0.1:8000/submit

# Cloud dry-run sandboxing (EKS + Falco)
EKS_ENABLED=true
FALCO_ENABLED=true
AWS_PROFILE=cloudproject-sydney
AWS_REGION=ap-southeast-2
KUBECONFIG=C:\Users\<username>\.kube\config-cloudproject-sydney   # Absolute path on your device
EKS_CLUSTER_NAME=cloudproject-sandbox-sydney
EKS_NAMESPACE=cloudproject-sandbox
EKS_IMAGE=562358727339.dkr.ecr.ap-southeast-2.amazonaws.com/cloudproject-sandbox@sha256:4b4ff4487b322521c75249cf461e1b439f048d0ab91024cf422509176ae22ea7
```

> [!NOTE]
> * Use `GATEWAY_URL=http://127.0.0.1:8000/submit` for direct dashboard runs (`python -m src.dashboard`); use port `8080` only when NGINX is fronting the gateway.
> * For full details, see the platform-specific guides: [Windows Guide](docs/eks-teammate-setup-windows.md) and [macOS/Linux Guide](docs/eks-teammate-setup.md).
> * The endpoint also requires the selected runtime locally: Python, Bash (Git for Windows supplies it), or PowerShell. Note that PowerShell currently triggers Falco startup syscall findings and is not a clean demo case.

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

### Streamlined 3-View Navigation

The dashboard provides a focused 3-view navigation bar in the sidebar:
1. **Workbench (Default view):** The core operator console containing the script editor, payload submission presets, pipeline visualizer, live execution output, and automated defense checks.
2. **Activity:** The full historical audit journal and event log, recording every submission's status code, execution duration, and JSON response payload.
3. **System status:** Real-time health monitor displaying local perimeter service statuses (Gateway, Endpoint, Central Monitor), AWS EKS cluster connectivity, and the Falco behavioral monitor, alongside a 2x2 cluster performance telemetry grid (Submission Throughput, Queue Capacity & Admission, Average Stage Latencies, and Failure Isolation).

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

### 2.1 One-Click Automated Pipeline Checks

The dashboard includes three automated defense checks directly on the Workbench page to rapidly verify each layer of defense:

| Test Scenario | Defense Layer | Action Button | How It Operates | Expected Pipeline Outcome |
| :--- | :--- | :--- | :--- | :--- |
| **Signature Tamper Test** | **Stage 1: Integrity Verification** | Click **"Run Tamper Test"** | Loads `good.py` in tamper mode. Click **"Run pipeline"** to submit. | Immediate rejection at Stage 1 (`HTTP 401 Unauthorized`). Payload signature is corrupted in transit; modified code fails closed before consuming cloud resources. |
| **Sandbox Resource Cap** | **Stage 3: EKS Isolation & Falco** | Click **"Run Loop Canary"** | Loads `infinite_loop.py` in signed mode. Click **"Run pipeline"** to submit. | Rejection at Stage 3 (`HTTP 422 Unprocessable Content`). Submits a CPU-bound infinite loop; the Kubernetes sandbox Job is terminated at its 10-second deadline without reaching the edge endpoint. |
| **Queue Admission Test** | **Stage 2: Cloud Capacity Controller** | Click **"Run Queue Test"** | Automatically dispatches 5 concurrent probes in parallel. | Concurrency bounding (`HTTP 503 Service Unavailable`). Exactly 3 probes are admitted into the EKS queue (max concurrency limit); the remaining 2 are shed cleanly. |

> [!NOTE]
> **Cloud Hosting & Independence:** The EKS cluster runs 24/7 on AWS cloud infrastructure in Sydney (`ap-southeast-2`). It does **not** depend on Rance's (or anyone else's) laptop being on. Teammates communicate directly with AWS control plane and EC2 instances via their own AWS Builder ID session (`aws login --profile cloudproject-sydney`).

> [!TIP]
> **Queue Reset Utility:** If an EKS test run is abruptly stopped mid-flight, stale in-flight records can be reset to 0/3 active slots by running:
> ```powershell
> python scripts/clear_stale_submissions.py
> ```

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

### 5.1 Pytest Test Suite

The test suite covers unit, integration, and security layers. Tests that use mocked components run in standard local environments without requiring AWS or Docker.

#### 1. Dashboard Security & Integration Suite (13 tests):
Runs in ~4 seconds and verifies the web gateway, token isolation, and pipeline defense without external dependencies:
```powershell
python -m pytest src/tests/test_dashboard.py -v
```
* **Coverage:** Secret key leak prevention (HTML/JS assets never expose `SHARED_SECRET`), token authentication on `/api/sign`, loopback origin enforcement, pipeline replay and tamper protection, strict form validation, missing secret graceful degradation, and health endpoint cache/mutex safety.

#### 2. Core Test Suite (151 tests, No Docker required):
Skips the legacy local Docker container tests and runs all unit tests, crypto verifiers, and cloud queue logic:
```powershell
python -m pytest src/tests -v -m "not docker" -p no:cacheprovider
```

#### 3. Complete Test Suite (160 tests, Docker Desktop active):
```bash
docker build -t cloudproject-sandbox:dev docker/sandbox
python -m pytest src/tests -v -p no:cacheprovider
```

#### Test Suite Module Breakdown:
| Test Module | Tests | Layer Tested | Validation Goal |
| :--- | :--- | :--- | :--- |
| `test_dashboard.py` | 13 | Web UI & API Gateway | Token validation, asset security, form bounds, health caching |
| `test_gateway.py` | 15 | Ingress & Dispatcher | HMAC authentication, nonce tracking, admission control |
| `test_watchdog.py` | 12 | Cryptographic Watchdog | HMAC-SHA256 signature verification, timing attack resistance |
| `test_cloud_submissions.py` | 8 | Cloud Queue & Store | SQLite queue limits, atomic claims, worker leases, heartbeats |
| `test_eks_runner_manifest.py`| 10 | Kubernetes Sandbox | Job YAML rendering, seccomp profiles, read-only rootfs, quotas |
| `test_endpoint.py` | 8 | Edge Execution Host | Local script execution on loopback (:5000) |
| `test_falco_runner.py` | 14 | Sycall Behaviour Monitor | Rule matching, finding prioritization, verdict classification |
| `test_ingress.py` | 8 | Perimeter Guardrails | 64 KiB payload limits, timeout guards, response serialization |
| `test_sandbox.py` | 9 | Local Container Isolation | Docker sandbox resource bounding (requires Docker Desktop) |

---

### 5.2 End-to-End Live Pipeline Scenarios

With the gateway and dashboard running (`python -m src.dashboard`), execute the full suite of end-to-end scenarios via CLI to verify each defense layer against the live Sydney EKS cluster:

| # | Test Scenario | CLI Command | Expected Outcome | Defense Layer Exercised |
| :--- | :--- | :--- | :--- | :--- |
| **1** | **Golden Path (Python)** | `python -m src.sender.send src/examples/good.py` | **HTTP 200 OK** (`delivered: true`) | Stage 1 HMAC verified → Stage 2 Queue admitted → Stage 3 EKS clean dry run → Stage 4 Endpoint delivery |
| **2** | **Golden Path (Bash)** | `python -m src.sender.send src/examples/good.sh` | **HTTP 200 OK** (`delivered: true`) | Polyglot Bash execution cleanly isolated in Kubernetes sandbox container |
| **3** | **Tampered Payload** | `python -m src.sender.send src/examples/good.py --tamper` | **HTTP 401 Unauthorized** | **Stage 1 Integrity:** Payload modified after signing; fails closed immediately without consuming cloud resources |
| **4** | **Replay Attack** | `python -m src.sender.send src/examples/good.py --replay` | 1st **HTTP 200/202**, 2nd **HTTP 401** | **Stage 1 Nonce:** Second submission uses identical cryptographic nonce; rejected as duplicate replay |
| **5** | **Infinite Loop** | `python -m src.sender.send src/examples/infinite_loop.py` | **HTTP 422 Unprocessable** (`timeout`) | **Stage 3 Resource Cap:** CPU exhaustion terminated at 10-second sandbox deadline; edge endpoint protected |
| **6** | **Memory Exhaustion** | `python -m src.sender.send src/examples/memory_hog.py` | **HTTP 422 Unprocessable** (`oom_killed`) | **Stage 3 Isolation:** Memory allocation capped at 128 MiB container limit; pod OOM-killed |
| **7** | **Syscall Evasion** | `python -m src.sender.send src/examples/network_swallowed.py` | **HTTP 422 Unprocessable** (`threat_detected`) | **Stage 3 Falco:** Script attempts network socket and swallows error; Falco kernel sensor flags socket syscall and blocks delivery |
| **8** | **Falco Cluster Canary** | `python -m scripts.check_falco` | **Canary Verified** | Queries worker nodes to confirm the Falco DaemonSet is active and publishing security alerts |
| **9** | **Queue Admission Cap** | Trigger via Dashboard **"Run Queue Test"** | 3 Admitted (**202**), 2 Rejected (**503**) | **Stage 2 Capacity:** Dispatches 5 concurrent probes; 3 fill max queue, excess shed cleanly with HTTP 503 |

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
