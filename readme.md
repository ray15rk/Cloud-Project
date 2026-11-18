# Secure Dynamic Payload Delivery Pipeline (Relay)

**Team 22 | Cloud & Distributed Computing**

A multi-tier, defense-in-depth distributed framework for safely pushing and executing untrusted mobile code on edge devices. Supports polyglot payloads (**Python**, **Bash / Shell scripts**, and **PowerShell**). Payloads undergo constant-time HMAC-SHA256 verification, EKS dry-run sandboxing, and delivery to target endpoints only after passing all checks.

> [!NOTE]
> **Evaluation & Cloud Access Notice:**  
> * **Offline Testing:** The core pipeline and automated test suite (`pytest src/tests -v`) run completely offline on any machine without requiring AWS credentials or Docker.
> * **Live Cloud Tier:** Cloud sandboxing executes on a dedicated, 24/7 AWS EKS cluster in Sydney (`ap-southeast-2`). To test the live cloud dashboard or submit live EKS jobs, evaluators and teammates require an email invitation from the project owner via AWS Builder ID (see [Section 1.4](#section-14-eks-access)).

---

## 1. Prerequisites & Initial Setup (Do This First!)

Before submitting payloads through the dashboard, complete these one-time setup steps. The automated tests in section 5.1 can run without AWS access.

> [!IMPORTANT]
> **EKS access and the sandbox image digest must be configured before testing payloads through the gateway.** When EKS is disabled, the gateway fails closed with HTTP 503.

Run all commands from the repository root directory.

### 1.1 Install System Dependencies
* **Python 3.11+**
* **AWS CLI v2.32.0 or newer and kubectl** for live access to the existing EKS cluster
* **NGINX** *(optional for local dashboard, required for production perimeter benchmark)*:
  * **Windows:** Download stable `.zip` from [nginx.org](https://nginx.org) and extract to `C:\nginx`.
  * **macOS:** `brew install nginx`
  * **Linux:** `sudo apt install nginx`

### 1.2 Environment & Dependencies Setup
Create and activate a virtual environment:

**Windows (PowerShell):**
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 1.3 Configure Secrets (`.env`)
Copy the template and generate a cryptographic signing secret:

**Windows (PowerShell):**
```powershell
if (-not (Test-Path .env)) { Copy-Item env.example .env }
python -c "import secrets; print(secrets.token_hex(32))"
```

**macOS / Linux:**
```bash
test -f .env || cp env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Paste the generated output as `SHARED_SECRET` inside `.env`.  
*(Never commit `.env` to version control).*

---

<a id="section-14-eks-access"></a>
### 1.4 Configure EKS & AWS Access (3 Fast Steps)

Relay runs disposable dry-run sandboxes on the Sydney EKS cluster (`cloudproject-sandbox-sydney` in `ap-southeast-2`, account `562358727339`). Teammates do not need static IAM credentials; access is granted via AWS Project sharing and AWS Builder ID.

#### Step 1: Request & Accept Project Invitation
1. Have the **EKS / AWS project owner** invite your email address once from the AWS Console (Projects > Invite Member).
2. Accept the invitation email using your **AWS Builder ID** at [settings.aws.com/projects](https://settings.aws.com/projects). Once accepted, you can proceed below without needing any further setup from the owner.

#### Step 2: Login and Generate Kubeconfig

> [!NOTE]
> **AWS Sign-in Page Note:** When your browser opens, AWS may display a legacy *"IAM user sign in"* screen. If so, click the **"Change to new experience"** button in the top-right banner (or log in directly at [settings.aws.com/projects](https://settings.aws.com/projects) first) to switch to the updated portal. This reveals the **AWS Builder ID** sign-in option. Once signed in, select Region **`ap-southeast-2`** and the invited project (`cloudproject-sandbox-sydney`, Account `562358727339`).

**Windows (PowerShell):**
```powershell
# 1. Login with your AWS Builder ID:
aws login --profile cloudproject-sydney

# 2. Generate cluster kubeconfig:
$Kubeconfig = Join-Path $env:USERPROFILE '.kube\config-cloudproject-sydney'
New-Item -ItemType Directory -Force (Split-Path $Kubeconfig) | Out-Null
aws eks update-kubeconfig --profile cloudproject-sydney --region ap-southeast-2 --name cloudproject-sandbox-sydney --alias cloudproject-sandbox-sydney --kubeconfig $Kubeconfig

# 3. Test cluster connection:
kubectl --kubeconfig $Kubeconfig -n cloudproject-sandbox get pods
```
*(Expected output: `No resources found in cloudproject-sandbox namespace.`)*

**macOS / Linux:**
```bash
# 1. Login with your AWS Builder ID:
aws login --profile cloudproject-sydney

# 2. Generate cluster kubeconfig:
export KUBECONFIG="$HOME/.kube/config-cloudproject-sydney"
mkdir -p "$(dirname "$KUBECONFIG")"
aws eks update-kubeconfig --profile cloudproject-sydney --region ap-southeast-2 --name cloudproject-sandbox-sydney --alias cloudproject-sandbox-sydney --kubeconfig "$KUBECONFIG"

# 3. Test cluster connection:
kubectl --context cloudproject-sandbox-sydney -n cloudproject-sandbox get pods
```
*(Expected output: `No resources found in cloudproject-sandbox namespace.`)*

> [!TIP]
> **Troubleshooting: If `kubectl` times out (`i/o timeout` on port 443):**  
> For defense-in-depth, the EKS cluster restricts public control-plane API access to authorized teammate CIDRs. If you are on a new machine, new Wi-Fi, or mobile hotspot, check your current public IP and run this one-liner to add it automatically (no need to ask the cluster owner):
> ```powershell
> $myIp = (Invoke-WebRequest -Uri "https://checkip.amazonaws.com" -UseBasicParsing).Content.Trim()
> $current = (aws eks describe-cluster --name cloudproject-sandbox-sydney --profile cloudproject-sydney --query "cluster.resourcesVpcConfig.publicAccessCidrs" --output text) -split "`t"
> $updated = ($current + "$myIp/32" | Select-Object -Unique) -join ","
> aws eks update-cluster-config --name cloudproject-sandbox-sydney --resources-vpc-config publicAccessCidrs="$updated" --profile cloudproject-sydney
> ```
> *(Optional: To remove your IP when testing is finished: `$reverted = ($current | Where-Object { $_ -ne "$myIp/32" }) -join ","; aws eks update-cluster-config --name cloudproject-sandbox-sydney --resources-vpc-config publicAccessCidrs="$reverted" --profile cloudproject-sydney`)*  
> Wait ~1 minute for AWS to apply, then re-run the `kubectl` test.

#### Step 3: Populate `.env`
Ensure your local `.env` has the verified settings pointing to your generated kubeconfig and cluster (replace `<username>` with your Windows user):

```dotenv
# Core pipeline configuration
SHARED_SECRET=<your_generated_32_byte_hex_secret>
GATEWAY_URL=http://127.0.0.1:8000/submit
ENDPOINT_URL=http://127.0.0.1:5000/execute
NOTIFY_URL=http://127.0.0.1:9000/notify

# Cloud dry-run sandboxing (EKS + Falco)
EKS_ENABLED=true
FALCO_ENABLED=true
AWS_PROFILE=cloudproject-sydney
AWS_REGION=ap-southeast-2
KUBECONFIG=C:\Users\<username>\.kube\config-cloudproject-sydney   # Absolute path on your device
EKS_CONTEXT=cloudproject-sandbox-sydney
EKS_IMAGE=562358727339.dkr.ecr.ap-southeast-2.amazonaws.com/cloudproject-sandbox@sha256:a2a31397c0f6a17856138c71ad14300b1e592ccc46e3ee0f61d7167911c5bffa
```

---

## 2. Quickstart: Interactive Web Dashboard

Once EKS access is configured, start the local dashboard:

Ensure ports `5000`, `8000`, and `9000` are free, then run:

**Windows (PowerShell):**
```powershell
.\.venv\Scripts\python.exe -m src.dashboard
```

**macOS / Linux:**
```bash
.venv/bin/python -m src.dashboard
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
3. **System status:** Real-time health monitor displaying local perimeter service statuses (Gateway, Endpoint, Central Monitor), AWS EKS cluster connectivity, and the Falco behavioral monitor, alongside a 2×2 cluster performance telemetry grid (Submission Throughput, Queue Capacity & Admission, Average Stage Latencies, and Failure Isolation).

### How to Test in the Dashboard:
1. **Load Sample Scripts:** Use the **Load sample** dropdown to select presets across all 3 languages (9 samples total):
   * **Python:** `Python: Healthy update` (`good.py`), `Python: Infinite loop` (`infinite_loop.py`), `Python: Memory exhaustion` (`memory_hog.py`).
   * **Bash:** `Bash: Healthy update` (`good.sh`), `Bash: Infinite loop` (`infinite_loop.sh`), `Bash: Memory exhaustion` (`memory_hog.sh`), `Bash: Network breach probe` (`network_breach.sh`), `Bash: Filesystem breakout` (`filesystem_breakout.sh`).
   * **PowerShell:** `PowerShell: Healthy update` (`good.ps1` — currently blocked by Falco startup findings).
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
> **Cloud Hosting & Independence:** The EKS cluster runs 24/7 on AWS cloud infrastructure in Sydney (`ap-southeast-2`). It does **not** depend on the project owner's (or anyone else's) laptop being on. Teammates communicate directly with AWS control plane and EC2 instances via their own AWS Builder ID session (`aws login --profile cloudproject-sydney`).

> [!TIP]
> **Queue Reset Utility:** If an EKS test run is abruptly stopped mid-flight, stale in-flight records can be reset to 0/3 active slots by running:
> ```powershell
> python scripts/clear_stale_submissions.py
> ```

*Press `Ctrl + C` in your terminal to stop all dashboard services.*

---

## 3. Polyglot Architecture & Symmetrical Test Cases

Relay supports heterogeneous edge workloads: **Bash / Shell scripts (`.sh`)** for hardware and OS diagnostics, **Python scripts (`.py`)** for telemetry processing and data analysis, and **PowerShell scripts (`.ps1`)** for administrative tasks.

### 3.1 Supported Language Runtimes

| Runtime | Key | Extension | Internal Container Command | Edge Fleet Use Case |
| :--- | :--- | :--- | :--- | :--- |
| **Python** | `python` | `.py` | `python3 -I -B -u /sandbox/script.py` | Telemetry processing, data aggregation, automation |
| **Bash / Shell** | `bash` | `.sh` | `bash /sandbox/script.sh` | OS diagnostics, kernel probing, daemon health checks |
| **PowerShell** | `powershell` | `.ps1` | `pwsh -NoProfile -File /sandbox/script.ps1` | Heterogeneous Windows/Linux administration tasks |

### 3.2 Symmetrical Test Cases (`src/examples/`)

Accepted submissions first return HTTP 202. The sender polls the status URL. The completed status endpoint returns HTTP 200 with the final outcome in its `status_code` field (200, 422, or 503); the CLI prints that outcome as “final”.

| Scenario | Example | Expected final outcome |
| --- | --- | --- |
| Clean Python or Bash | `good.py`, `good.sh` | 200, delivered after a clean Falco verdict |
| Infinite loop | `infinite_loop.py`, `infinite_loop.sh` | 422, timeout or a Falco finding |
| Memory exhaustion | `memory_hog.py`, `memory_hog.sh` | 422, memory failure or a Falco finding |
| Network attempt that catches its error | `network_swallowed.py` | 422, Falco network finding, no delivery |
| File write attempt | `fs_write_swallowed.py` | 422, Falco filesystem finding, no delivery |
| PowerShell startup | `good.ps1` | Currently blocked by runtime-generated Falco findings |

A monitoring failure can instead produce 503. Falco findings take precedence over script exit status. Upload the Python canaries through the dashboard or use the CLI.

---

## 4. Production NGINX Ingress & CLI Stack

In production mode, client submissions target the NGINX Ingress at **`http://127.0.0.1:8080/submit`**. Port `8000` is kept internal to the gateway.

Ensure your `.env` specifies: `GATEWAY_URL=http://127.0.0.1:8080/submit`

### 4.1 Manual Multi-Terminal Startup
Activate the virtual environment in each terminal, then run:

| Terminal | Service | Command | Description |
| :--- | :--- | :--- | :--- |
| **1** | Central Monitor | `python -m src.sender.central_server` | Listens for failure reports on `:9000` |
| **2** | Endpoint Device | `python -m src.endpoint.endpoint_app` | Downstream execution host on `:5000` |
| **3** | Production Gateway | `python -m src.gateway.serve` | Waitress WSGI (1 process, 8 HTTP threads; default 2 EKS workers, 3 outstanding submissions) on `:8000` |
| **4** | NGINX Proxy | *(See commands below)* | Perimeter rate/conn proxy on `:8080` |

#### Starting NGINX:
**Windows (PowerShell):**
```powershell
$nginxPrefix = (Join-Path (Get-Location).Path 'nginx').Replace('\', '/') + '/'
& 'C:\nginx\nginx.exe' -t -p $nginxPrefix -c nginx.conf
& 'C:\nginx\nginx.exe' -p $nginxPrefix -c nginx.conf

# To stop:
& 'C:\nginx\nginx.exe' -p $nginxPrefix -c nginx.conf -s quit
```

**macOS / Linux:**
```bash
nginx -t -p "$PWD/nginx/" -c nginx.conf
nginx -p "$PWD/nginx/" -c nginx.conf

# To stop:
nginx -p "$PWD/nginx/" -c nginx.conf -s quit
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
* **Coverage:** Secret key leak prevention (HTML/JS assets never expose `SHARED_SECRET`), token authentication on `/api/run` and `/api/capacity-test`, loopback origin enforcement, pipeline replay and tamper protection, strict form validation, missing secret graceful degradation, and health endpoint cache/mutex safety.

#### 2. Complete Automated Test Suite (151 tests — Offline, No AWS or Docker required):
Runs all unit tests, cryptographic verifiers, Falco rules/monitor checks, NGINX config assertions, and cloud queue logic:
```powershell
python -m pytest src/tests -v -p no:cacheprovider
```

#### Test Suite Module Breakdown (151 Tests Total):
| Test Module | Tests | Layer Tested | Validation Goal |
| :--- | :--- | :--- | :--- |
| `test_falco_monitor.py` | 34 | Syscall Behaviour Monitor | Alert parsing, fail-closed logic, flood handling, verdict classification |
| `test_falco_rules.py` | 15 | Falco Ruleset | Rule YAML structure and behaviour class coverage |
| `test_dashboard.py` | 13 | Web UI & API Gateway | Token validation, asset security, form bounds, health caching |
| `test_falco_runner.py` | 12 | EKS + Falco Integration | Runner + Falco end-to-end verdict mapping (mocked Kubernetes) |
| `test_watchdog.py` | 11 | Cryptographic Watchdog | HMAC-SHA256 integrity, freshness, replay prevention, language binding |
| `test_gateway.py` | 10 | Ingress & Dispatcher | Pipeline verify → EKS → Falco → deliver, fail-closed paths |
| `test_kube_logs.py` | 10 | Kubernetes Log Reader | Raw log retrieval over local loopback server (no cluster required) |
| `test_falco_real_client.py` | 8 | Falco API Client | Real Kubernetes client against local loopback mock server (no cluster required) |
| `test_nginx_conf.py` | 7 | NGINX Configuration | Path whitelist, 429 rate-limit code, 256 KiB cap, timeout, no-retry |
| `test_ingress.py` | 6 | Perimeter & Monitor | Sender error handling, no-retry, NGINX log aggregation & rotation |
| `test_serve.py` | 6 | WSGI Server Config | Thread/slot validation and dashboard toggle |
| `test_cloud_submissions.py` | 4 | Cloud Queue & Store | SQLite queue cap, atomic claims, worker lease recovery |
| `test_eks_runner_manifest.py` | 4 | Kubernetes Sandbox | Job YAML rendering, runtime args, kubeconfig token retry |
| `test_resource_manager.py` | 4 | Concurrency Controller | Atomic admission, memory budget tracking, slot release |
| `test_endpoint.py` | 3 | Edge Execution Host | Local script execution, UTF-8 output, failure cleanup |
| `test_sandbox_job_template.py` | 3 | Job Template Manifest | Explicit command/args and Dockerfile entrypoint checks |
| `test_no_conflict_markers.py` | 1 | Repository Hygiene | Verifies no unresolved Git merge conflict markers |

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

## 6. Behavioural Threat Detection (Falco)

The sandbox's own checks only see the **outcome** of a dry run (timeout, OOM, exit code). A script that tries the network, catches the error and exits 0 looks harmless. [Falco](https://falco.org) runs on the EKS nodes, outside the sandbox, and reports what a script actually *attempted* at the syscall level: network sockets, file writes, other programs, credential paths, privileged syscalls. The gateway rejects the payload (`HTTP 422`, `threat_detected`) before it is forwarded, and fails closed (`HTTP 503`) if Falco did not observe the run.

```bash
python -m scripts.check_falco                          # live canary test against your cluster
python -m src.sender.send src/examples/network_swallowed.py   # exits 0 itself, still rejected (422)
```

Rules and policy lists: [`infra/falco/cloudproject_rules.yaml`](infra/falco/cloudproject_rules.yaml).

---

## 7. Known Limitations

| Area | Limitation & Workaround |
| :--- | :--- |
| **PowerShell in Cloud Mode** | Blocked under EKS by Falco DaemonSet because `pwsh` engine startup generates file and network syscall events that trigger benign security alerts. Python and Bash run cleanly. `EKS_ENABLED=false` rejects dry-run submissions. |
| **Intermittent Cloud HTTP 503** | Falco log retrieval over Kubernetes API can occasionally experience timing latency where the gateway does not observe `CP Sandbox Observed` within the settle deadline, failing closed with HTTP 503. This is an infrastructure polling latency, not a malicious code detection. Retry the submission. |
| **Live Falco Client Tests** | `test_falco_real_client.py` and `test_kube_logs.py` use local loopback test servers; they do not require EKS credentials. |

---

## 8. Directory Structure

```
relay/
├── docker/sandbox/       # Container Dockerfile & entrypoint runner for the EKS sandbox image
├── docs/                 # Operational guides: AWS EKS, Windows/Linux teammate setup, Falco setup, NGINX
├── infra/
│   ├── eks/              # IAM & node autoscaler policies, cluster launch configuration
│   ├── falco/            # DaemonSet values, Kubernetes RBAC, and cloudproject_rules.yaml
│   └── k8s/              # Disposable sandbox Job template, namespace quota, NetworkPolicies
├── nginx/                # Ingress reverse proxy configuration (rate limiting, timeouts, body cap)
├── scripts/              # Health-check, Falco canary, and queue reset utilities
├── src/
│   ├── dashboard.py      # Integrated 3-in-1 server runner (Gateway + Endpoint + Monitor)
│   ├── ingress.py        # System-wide configuration timeouts and constants
│   ├── endpoint/         # Mock edge endpoint application (:5000/execute)
│   ├── examples/         # Symmetrical sample scripts (Python, Bash, PowerShell; healthy & adversarial)
│   ├── gateway/          # Flask gateway, HMAC verification, SQLite cloud queue, and live UI
│   ├── resource_manager/ # Atomic slot reservation & concurrency controller
│   ├── sandbox/          # EKS Job runner, Falco syscall log monitor, and Kubernetes log reader
│   ├── sender/           # Signed payload generator CLI and centralized monitor inbox (:9000)
│   ├── tests/            # Pytest suite (17 modules, 151 tests — unit, integration, adversarial)
│   └── watchdog/         # Canonical HMAC-SHA256 signing, verification, and TTL NonceStore
├── .gitattributes        # Enforces Unix LF line endings on shell scripts (.sh)
├── changelog.md          # Project milestone and engineering changelog
├── env.example           # Environment variable template with placeholders
├── readme.md             # Authoritative project documentation & execution guide
└── requirements.txt      # Production runtime dependencies
```
