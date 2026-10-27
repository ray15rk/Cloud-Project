# Use the Sydney EKS cluster from macOS or Linux

This guide runs a local copy of the gateway against the existing
`cloudproject-sandbox-sydney` cluster in `ap-southeast-2`. It does not create
a new AWS cluster. For Windows, use the [PowerShell guide](eks-teammate-setup-windows.md).
See [AWS access](aws-setup.md) and the [team handoff](eks-team-handoff.md)
for the local/cloud boundary.

## 1. Prepare the local project

Install Python 3.11+, Docker Desktop, AWS CLI v2, and a `kubectl` client
compatible with the cluster's Kubernetes 1.36 version. From the repository
root, install dependencies and build the local sandbox image:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
docker build -t cloudproject-sandbox:dev docker/sandbox
```

Copy `env.example` to the gitignored `.env` if needed and generate a unique
`SHARED_SECRET`. Never copy someone else's secret.

## 2. Confirm EKS access

Use your own authorized AWS CLI profile. The operator must grant your identity
Kubernetes access to this cluster and allow your current public IP through its
API endpoint settings. A kubeconfig file by itself grants neither permission.
Replace `YOUR_PROFILE` and the kubeconfig path with your own values:

```sh
aws sts get-caller-identity --profile YOUR_PROFILE
mkdir -p "$HOME/.kube"
aws eks update-kubeconfig --profile YOUR_PROFILE \
  --region ap-southeast-2 --name cloudproject-sandbox-sydney \
  --alias cloudproject-sandbox-sydney \
  --kubeconfig "$HOME/.kube/config-cloudproject-sydney"
KUBECONFIG="$HOME/.kube/config-cloudproject-sydney" \
  kubectl --context cloudproject-sandbox-sydney get nodes
```

Verify the account ID is `562358727339` and that at least one node is Ready.
If authentication or network access fails, resolve it before enabling cloud
overflow. Do not share AWS credentials or put the public IP allowlist in Git.

## 3. Configure and test the gateway

In your gitignored `.env`, set `EKS_ENABLED=true`,
`EKS_CONTEXT=cloudproject-sandbox-sydney`, `AWS_REGION=ap-southeast-2`,
`AWS_PROFILE` to your own profile, and `KUBECONFIG` to your own absolute
kubeconfig path. Set `EKS_IMAGE` to the immutable Sydney ECR digest shown in
[deployment details](../infra/eks/README.md). The EKS worker pulls that
private image using its node role. Set `GATEWAY_URL` to port 8080 if you start
the project's Nginx proxy, or port 8000 for direct local testing.

With Docker Desktop running, start the dashboard from the repository root:

```sh
.venv/bin/python -m src.dashboard
```

In another terminal, submit the example:

```sh
.venv/bin/python -m src.sender.send src/examples/good.py
```

A normal submission should report `execution_location: local`. To demonstrate
cloud overflow, stop the dashboard with Ctrl+C and restart it with a temporary
small local budget:

```sh
LOCAL_MEMORY_BUDGET_MB=1 .venv/bin/python -m src.dashboard
```

Send the example again. Expect HTTP 202 followed by a final result with
`execution_location: eks`. Stop and restart the dashboard normally afterward.

For local-only use, set `EKS_ENABLED=false`; no AWS access is needed. Cluster
Autoscaler runs in EKS and scales worker nodes when Pods cannot be scheduled.
The local gateway only decides whether a submission uses local Docker or EKS.
