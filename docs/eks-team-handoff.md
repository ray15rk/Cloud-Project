# EKS team handoff

## What runs where

This is a cloud-first dry-run demonstration. The gateway, dashboard, Nginx
proxy, endpoint, and notification server run on the operator's computer. The
EKS cluster `cloudproject-sandbox-sydney` runs dry-run Jobs in AWS region
`ap-southeast-2`. Hybrid local Docker overflow remains available as an optional mode.

```text
Browser / sender CLI
       |
       v
Local Nginx proxy on 127.0.0.1:8080
       |
       v
Local gateway on 127.0.0.1:8000 -- verifies the signed payload
       |
       +------------------------------> EKS Job dry run in AWS
                                          | Falco observes on the EKS node
                                          v
                                    result and verdict returned
       |
       v
Local endpoint on 127.0.0.1:5000 -- receives a successful payload
```

With `EXECUTION_MODE=cloud`, every verified submission is queued for an EKS
dry-run Job and initially receives HTTP 202. After a clean result, the gateway
forwards the original payload to the local endpoint. A detected threat or an
unavailable monitor blocks delivery. The default limit is three outstanding
cloud submissions. `EXECUTION_MODE=hybrid` restores local Docker first, with
EKS used only when the local host is full.

Kubernetes Cluster Autoscaler handles a different decision: if a Pod cannot
fit on the current worker node, it can increase the managed node group's
desired capacity from one to two. It removes an unneeded extra node later.
The gateway no longer contains its own node scaler.

There is no cloud-hosted gateway, public application URL, Kubernetes Service,
or Ingress for the gateway. If the operator's computer is off, its dashboard
and endpoint are unavailable. AWS resources can still incur charges.

## Current AWS resources

- EKS cluster: `cloudproject-sandbox-sydney`, Kubernetes 1.36
- Region: `ap-southeast-2` (Sydney)
- Managed node group: `cloudproject-sandbox-nodes`
- Worker type and range: `t3.small`, minimum 1, maximum 2
- Sandbox namespace: `cloudproject-sandbox`
- Private ECR repository: `cloudproject-sandbox`
- Cluster Autoscaler: deployed in `kube-system` with a dedicated Pod Identity role

The sandbox namespace has a resource quota and restricted Pod Security
settings. Strict VPC CNI network policy enforcement blocks sandbox network
access. The worker node has the label `workload=cloudproject-sandbox`, which
the sandbox Job template selects.

## What was verified in Sydney

- A sandbox Job completed and printed `RUNTIME_OK`.
- Its attempted outbound network connection was blocked.
- A pending workload triggered a second worker node; both nodes became Ready.
- After the workload was removed, Cluster Autoscaler returned the group to
  one worker node.
- A gateway overflow submission returned HTTP 202, then completed with
  `execution_location: eks` and delivered its result to the local endpoint.
- A normal submission completed through the local path.
- On 2026-11-01, cloud-first Python and Bash submissions completed through the
  real sender, NGINX, EKS, and local endpoint. A network-attempt submission was
  blocked before delivery. Intermittent clean-run HTTP 503 failures remain an
  open reliability issue; PowerShell also triggers Falco runtime alerts.

These checks demonstrate the configured environment at test time. They do
not make it production ready. The endpoint runs a successful payload on its
own host after the dry run; only the dry run uses the sandbox.

## Run it on the operator's Mac

Keep AWS credentials outside the repository. The gitignored `.env` needs the
generated `SHARED_SECRET`, the project's local service URLs, and these EKS
settings:

```dotenv
EKS_ENABLED=true
EKS_CONTEXT=cloudproject-sandbox-sydney
EKS_IMAGE=562358727339.dkr.ecr.ap-southeast-2.amazonaws.com/cloudproject-sandbox@sha256:a2a31397c0f6a17856138c71ad14300b1e592ccc46e3ee0f61d7167911c5bffa
EKS_MAX_INFLIGHT=3
EXECUTION_MODE=cloud
FALCO_ENABLED=true
AWS_REGION=ap-southeast-2
AWS_PROFILE=cloudproject-sydney
KUBECONFIG=/Users/rance/.kube/config-cloudproject-sydney
GATEWAY_URL=http://127.0.0.1:8080/submit
```

From the repository root, start Docker Desktop and the dashboard:

```sh
.venv/bin/python -m src.dashboard
```

Open `http://127.0.0.1:8000`. If Nginx is already running on port 8080,
submit from a second terminal:

```sh
.venv/bin/python -m src.sender.send src/examples/good.py
```

In cloud-first mode, the normal submission above already uses EKS. To show the
older hybrid overflow path, restart with a temporary low local memory budget:

```sh
EXECUTION_MODE=hybrid LOCAL_MEMORY_BUDGET_MB=1 .venv/bin/python -m src.dashboard
```

Restart it normally afterward to restore the configured cloud-first mode.

## Ownership handoff

- **EKS owner:** cluster, node group and autoscaler, ECR image reference,
  sandbox namespace and Job template, and the EKS runner's Job lifecycle.
- **Falco owner:** detection rules, Falco deployment and alert interpretation.
  Falco runs on the EKS nodes but its policy is a separate responsibility.
- **Gateway owner:** signed language in the queue, worker scheduling, final
  delivery decision, endpoint forwarding, and dashboard response.

The shared interface is the EKS runner result: language selects the sandbox
interpreter; a clean monitored run returns `OK`; a Falco finding returns
`threat_detected`; unavailable monitoring or EKS returns
`infrastructure_error`. Only `OK` may be forwarded.

## Teammate access

A teammate can run the project in local-only mode with Docker, their own
generated secret, and `EKS_ENABLED=false`. This needs no AWS access.

To use the Sydney EKS cluster from another computer, the teammate needs an
authorized IAM identity in the same AWS account, Kubernetes access to the
cluster, and an API endpoint allowlist entry for their current public IP.
Copying a kubeconfig file or the repository does not grant access. They
should use their own AWS CLI profile and kubeconfig, then set their `.env`
values accordingly. Do not share AWS credentials or the gateway secret.

The ECR repository is private. EKS worker nodes pull the sandbox image using
their node IAM role. See the [AWS kubeconfig guide](https://docs.aws.amazon.com/eks/latest/userguide/create-kubeconfig.html)
and [EKS access entries](https://docs.aws.amazon.com/eks/latest/userguide/access-entries.html).

## Operating and cleanup

Commit source code, manifests, `env.example`, and documentation. Do not commit
`.env`, AWS credentials, kubeconfig files, database files, or the operator's
public IP allowlist.

The EKS control plane and worker resources can incur charges while they
exist, even when the local dashboard is stopped. For cluster checks and
cleanup guidance, see [`infra/eks/README.md`](../infra/eks/README.md).
If the operator's public IP changes, update the EKS API endpoint allowlist
before connecting again.
