# EKS team handoff

## What runs where

This is an EKS dry-run demonstration. The gateway, dashboard, Nginx
proxy, endpoint, and notification server run on the operator's computer. The
EKS cluster `cloudproject-sandbox-sydney` runs dry-run Jobs in AWS region
`ap-southeast-2`. Every verified dry run is queued as an EKS Job.

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

Every verified submission is queued for an EKS dry-run Job and initially
receives HTTP 202. After a clean result, the gateway
forwards the original payload to the local endpoint. A detected threat or an
unavailable monitor blocks delivery. The default limit is three outstanding
cloud submissions. When EKS is disabled, dry-run submissions fail closed.

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
- An EKS submission returned HTTP 202, then completed with
  `execution_location: eks` and delivered its result to the local endpoint.
- A normal submission completed through the local path during the earlier
  hybrid implementation; that route has since been removed.
- On 2026-11-01, cloud-first Python and Bash submissions completed through the
  real sender, NGINX, EKS, and local endpoint. A network-attempt submission was
  blocked before delivery. Intermittent clean-run HTTP 503 failures remain an
  open reliability issue; PowerShell also triggers Falco runtime alerts.
- On 2026-11-12, a direct-gateway clean Python submission completed through
  EKS and Falco and was delivered to the endpoint. A network-attempt canary
  returned HTTP 422 with a Falco threat verdict and was not delivered. No
  test Jobs or ConfigMaps remained afterward.

These checks demonstrate the configured environment at test time. They do
not make it production ready. The endpoint runs a successful payload on its
own host after the dry run; only the dry run uses the sandbox.

## Run it from your computer

Follow the [macOS/Linux](eks-teammate-setup.md) or
[Windows](eks-teammate-setup-windows.md) guide. Each covers your own AWS
profile and kubeconfig, `.env`, local services, and a cloud smoke test. The
ECR image digest is recorded in [deployment details](../infra/eks/README.md).

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

A teammate needs authorized EKS access to run gateway dry runs. With
`EKS_ENABLED=false`, the gateway rejects dry-run submissions.

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
