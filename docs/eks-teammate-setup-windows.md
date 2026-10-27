# Use the Sydney EKS cluster from Windows

Run these commands in PowerShell. This guide runs a local gateway against the
existing `cloudproject-sandbox-sydney` cluster in `ap-southeast-2`; it does
not create an AWS cluster. For macOS/Linux, use the [other guide](eks-teammate-setup.md).
Read [AWS access](aws-setup.md) before enabling cloud overflow.

## 1. Prepare the local project

Install Python 3.11+, Docker Desktop with Linux containers, AWS CLI v2, and a
`kubectl` client compatible with Kubernetes 1.36. Install Nginx if you want to
use the project's port 8080 proxy. From the repository root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
docker build -t cloudproject-sandbox:dev docker/sandbox
```

Copy `env.example` to `.env` if it does not exist. Generate your own
`SHARED_SECRET` and keep `.env` out of Git:

```powershell
if (-not (Test-Path .env)) { Copy-Item env.example .env }
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

## 2. Confirm EKS access

Use your own AWS CLI profile, Kubernetes authorization, and an API endpoint
allowlist entry for your current public IP. Replace `YOUR_PROFILE` with your
own profile name. These commands do not grant access:

```powershell
$Profile = 'YOUR_PROFILE'
$Kubeconfig = Join-Path $env:USERPROFILE '.kube\config-cloudproject-sydney'
aws sts get-caller-identity --profile $Profile
New-Item -ItemType Directory -Force (Split-Path $Kubeconfig) | Out-Null
aws eks update-kubeconfig --profile $Profile --region ap-southeast-2 --name cloudproject-sandbox-sydney --alias cloudproject-sandbox-sydney --kubeconfig $Kubeconfig
$env:KUBECONFIG = $Kubeconfig
kubectl --context cloudproject-sandbox-sydney get nodes
```

Verify the account ID is `562358727339` and a node is Ready. If AWS or
Kubernetes rejects access, ask the cluster operator to check your IAM/EKS
authorization and current IP allowlist. Do not share credentials or secrets.

## 3. Configure and test the gateway

Edit the gitignored `.env`. Set `EKS_ENABLED=true`,
`EKS_CONTEXT=cloudproject-sandbox-sydney`, `AWS_REGION=ap-southeast-2`,
`AWS_PROFILE` to your own profile, and `KUBECONFIG` to the absolute path in
`$Kubeconfig`. Replace the example file's macOS path. Set `EKS_IMAGE` to the
immutable ECR digest in [deployment details](../infra/eks/README.md).
Use `GATEWAY_URL=http://127.0.0.1:8000/submit` for direct local testing,
or port 8080 if the project Nginx proxy is running.

Start Docker Desktop and the dashboard from the repository root:

```powershell
.\.venv\Scripts\python.exe -m src.dashboard
```

In a second PowerShell window, submit:

```powershell
.\.venv\Scripts\python.exe -m src.sender.send src/examples/good.py
```

A normal submission should report `execution_location: local`. To force one
EKS test, stop the dashboard and restart it with a temporary local budget:

```powershell
$env:LOCAL_MEMORY_BUDGET_MB = '1'
.\.venv\Scripts\python.exe -m src.dashboard
```

Send the example again. Expect HTTP 202 followed by
`execution_location: eks`. Afterward, stop the dashboard, remove the temporary
override, and restart normally:

```powershell
Remove-Item Env:\LOCAL_MEMORY_BUDGET_MB
.\.venv\Scripts\python.exe -m src.dashboard
```

For local-only use, set `EKS_ENABLED=false`; no AWS access is needed. The
gateway selects local Docker or EKS, while Cluster Autoscaler independently
scales worker nodes for unschedulable Pods.
