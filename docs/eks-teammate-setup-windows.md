# EKS setup for Windows PowerShell

This guide connects your local gateway to the **existing**
`cloudproject-sandbox-sydney` EKS cluster in `ap-southeast-2`. It does not
create a new cluster. For macOS/Linux, use the [other guide](eks-teammate-setup.md).
See [AWS access](aws-setup.md) and [deployment details](../infra/eks/README.md).

## 1. Install and prepare the project

Install Python 3.11+, AWS CLI v2, and a
`kubectl` client compatible with the cluster version shown by
`aws eks describe-cluster`. From the repository root in PowerShell:

The `kubectl` client must be within one Kubernetes minor version of the
cluster. Check `kubectl version --client` after checking the cluster version;
for the deployed 1.36 cluster, use a 1.35, 1.36, or 1.37 client.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item env.example .env }
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

Put the generated value in `.env` as `SHARED_SECRET`. Preserve an existing
`.env` instead of overwriting it. Keep it out of Git. Docker is not needed
for gateway dry runs.

## 2. Get access to the existing cluster

The operator must grant your IAM identity EKS/Kubernetes access and allow
your current public IP to reach the cluster API. Replace `YOUR_PROFILE` with
**your own** AWS profile. Kubeconfig creation does not grant access:

```powershell
$Profile = 'YOUR_PROFILE'
$Kubeconfig = Join-Path $env:USERPROFILE '.kube\config-cloudproject-sydney'
aws sts get-caller-identity --profile $Profile
aws eks describe-cluster --profile $Profile --region ap-southeast-2 --name cloudproject-sandbox-sydney --query 'cluster.{status:status,version:version}'
New-Item -ItemType Directory -Force (Split-Path $Kubeconfig) | Out-Null
aws eks update-kubeconfig --profile $Profile --region ap-southeast-2 --name cloudproject-sandbox-sydney --alias cloudproject-sandbox-sydney --kubeconfig $Kubeconfig
$env:KUBECONFIG = $Kubeconfig
kubectl --context cloudproject-sandbox-sydney get nodes
```

The AWS identity must be in account `562358727339`; check that the cluster is
active and a node is Ready. If access fails, ask the operator to check your
access entry and API allowlist. If AWS reports an expired session, run
`aws login --profile $Profile` and repeat the identity check. If `kubectl`
times out, compare your current public IP with the cluster's API allowlist;
ask the operator to add only your current `/32` address for the test and remove
it afterward. Never share credentials or commit your IP.

Check the Falco Pod before enabling `FALCO_ENABLED=true`:

```powershell
kubectl --context cloudproject-sandbox-sydney -n falco get pods
```

Expect a Running Pod on each Ready worker node.

## 3. Configure the gateway

Edit `.env`, replacing the profile and kubeconfig placeholders with `$Profile`
and the **expanded absolute path** printed by `$Kubeconfig`. Set `EKS_IMAGE`
to the digest in [deployment details](../infra/eks/README.md):

```dotenv
EKS_ENABLED=true
EKS_CONTEXT=cloudproject-sandbox-sydney
EKS_IMAGE=PASTE-IMMUTABLE-ECR-IMAGE-AT-SHA256-DIGEST
AWS_REGION=ap-southeast-2
AWS_PROFILE=YOUR_PROFILE
KUBECONFIG=C:\Users\YOUR_USER\.kube\config-cloudproject-sydney
GATEWAY_URL=http://127.0.0.1:8000/submit
FALCO_ENABLED=true
```

Every verified dry run is queued as an EKS Job. With `FALCO_ENABLED=true`,
unavailable monitoring blocks delivery. `EKS_ENABLED=false` disables dry-run
submissions; the gateway never falls back to local Docker.

## 4. Run a smoke test

Start the dashboard from the repository root:

```powershell
.\.venv\Scripts\python.exe -m src.dashboard
```

Open `http://127.0.0.1:8000`. In a second PowerShell window:

```powershell
.\.venv\Scripts\python.exe -m src.sender.send src/examples/good.py
```

Expect initial HTTP 202 and a final result with `execution_location: eks`.
The final result should contain `delivered: true` and a clean Falco verdict.
The sender polls the returned status URL. To test a blocked network attempt:

```powershell
.\.venv\Scripts\python.exe -m src.sender.send src/examples/network_swallowed.py
```

Expect initial HTTP 202, then final HTTP 422 with `delivered: false`,
`execution_location: eks`, and a Falco network finding. Stop the dashboard
after testing. If temporary API access was granted, have the operator remove
that `/32` entry and verify the original allowlist is restored.

For the optional Nginx proxy, set
`GATEWAY_URL=http://127.0.0.1:8080/submit` and start Nginx separately using
the [project README](../readme.md).
