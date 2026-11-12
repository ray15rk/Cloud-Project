# EKS deployment

Region: `ap-southeast-2`. Cluster: `cloudproject-sandbox-sydney`.
The cluster runs Kubernetes 1.36 in Rance's personal AWS account. Its public
API endpoint is limited to the operator's current public IP; private endpoint
access is also enabled. Do not commit AWS credentials, secrets, or the current
public IP.

## Deployed resources

- Managed node group: `cloudproject-sandbox-nodes`
- Worker instance type: `t3.small`, On-Demand
- Node group size: minimum 1, desired 1, maximum 2
- Node label: `workload=cloudproject-sandbox`
- Sandbox namespace: `cloudproject-sandbox`
- Private ECR repository: `cloudproject-sandbox`
- Kubernetes Cluster Autoscaler: installed in `kube-system` using EKS Pod Identity

The autoscaler was installed with Helm chart `9.59.0` and a Kubernetes 1.36
autoscaler image. Its service account is `kube-system/cluster-autoscaler`,
associated with the dedicated IAM role
`cloudproject-eks-cluster-autoscaler-sydney`. The ASG has the discovery tags
`k8s.io/cluster-autoscaler/enabled=true` and
`k8s.io/cluster-autoscaler/cloudproject-sandbox-sydney=owned`. The policy in
`autoscaler-node-policy.json` limits scaling actions to ASGs with those tags;
it belongs on the autoscaler role, not the worker node role.

The node launch template requires IMDSv2 and sets its metadata response hop
limit to one. The node group spans three Sydney subnets. Kubernetes Cluster
Autoscaler watches for Pods that cannot be scheduled, increases the managed node
group's desired size when needed, and removes an unneeded extra node later.
The local gateway does not scale nodes.

The sandbox namespace has a resource quota, restricted Pod Security settings,
and a network policy. VPC CNI network policy enforcement is set to strict.
CoreDNS has a separate policy so DNS continues to work.

## Check the cluster

Use your own authorized AWS CLI profile and kubeconfig. Replace the
placeholders below; see the [macOS/Linux](../../docs/eks-teammate-setup.md)
or [Windows](../../docs/eks-teammate-setup-windows.md) setup guide to create
your kubeconfig:

```sh
aws eks describe-cluster --profile YOUR_PROFILE \
  --region ap-southeast-2 --name cloudproject-sandbox-sydney \
  --query 'cluster.{status:status,version:version}'

aws eks list-nodegroups --profile YOUR_PROFILE \
  --region ap-southeast-2 --cluster-name cloudproject-sandbox-sydney

KUBECONFIG=/absolute/path/to/your/kubeconfig \
  kubectl --context cloudproject-sandbox-sydney get nodes -L workload
KUBECONFIG=/absolute/path/to/your/kubeconfig \
  helm --kube-context cloudproject-sandbox-sydney list -n kube-system
```

Set `KUBECONFIG` to your own absolute path in `.env` before running the gateway.

If a run fails before creating a Job, first check `aws sts get-caller-identity
--profile YOUR_PROFILE`. An expired AWS login can make the kubeconfig's
credential command fail. The EKS runner retries a missing token three times and
then logs `Kubernetes client setup` with an authentication error. Refresh your
session using your AWS sign-in method before retrying.

## Sandbox image

The sandbox image is stored in the Sydney ECR repository. Use the image's
immutable digest rather than a mutable tag:

```text
562358727339.dkr.ecr.ap-southeast-2.amazonaws.com/cloudproject-sandbox@sha256:a2a31397c0f6a17856138c71ad14300b1e592ccc46e3ee0f61d7167911c5bffa
```

This Python/Bash/PowerShell image digest was verified in ECR on 2026-11-01.
Confirm it is still available before use. Update `EKS_IMAGE` when a new image
is built and pushed.

## Run a local gateway against EKS

Follow the [macOS/Linux](../../docs/eks-teammate-setup.md) or
[Windows](../../docs/eks-teammate-setup-windows.md) guide for complete install,
per-user configuration, and smoke-test commands. Every verified dry run is
queued as an EKS Job. Submissions return HTTP 202 while queued, followed by a
result with `execution_location: eks` when complete.

The gateway's EKS queue and Cluster Autoscaler's node scaling are separate.
Cluster Autoscaler adds a worker node only when Kubernetes cannot schedule a
Pod on existing nodes.

## Verified behavior

- A sandbox Job completed and printed `RUNTIME_OK`.
- The sandbox Job's attempted outbound network connection was blocked.
- A pending workload caused Cluster Autoscaler to add a second worker node.
- After the workload was deleted, Cluster Autoscaler removed the extra node.
- A gateway submission completed successfully through EKS and returned
  `execution_location: eks`.
- On 2026-11-12, a clean direct-gateway submission returned HTTP 202 then
  HTTP 200 after an EKS/Falco dry run; a network-attempt canary returned HTTP
  422 without endpoint delivery. The EKS runner now loads the configured
  `KUBECONFIG` path explicitly.
- On 2026-11-01, clean Python and Bash submissions completed through the real
  sender, NGINX, gateway, EKS Job, and local endpoint. A network-attempt script
  was rejected by Falco before endpoint delivery.
- Intermittent clean-run HTTP 503 failures were observed. On 2026-11-02, a clean Python
  Job failed closed after the full 120-second Falco wait, although its `CP Sandbox
  Observed` alert was visible in the Falco log afterward. The deployed Falco
  ConfigMap already has `buffered_outputs: false`. Darius's raw Kubernetes log
  reader fix was merged on 2026-11-07; three consecutive clean Python EKS canaries
  then passed with the start alert observed in about four seconds, and a network
  attempt was still detected as a threat. This limited run did not reproduce the
  earlier failure. A separate short EKS runner failure produced no Job; missing
  AWS tokens are now detected and retried, but the cause of those earlier
  failures was not captured.

## Costs and shutdown

The EKS control plane, worker nodes, storage, and other used AWS resources can
incur charges while they exist. Scaling back to one node does not stop all
charges. When the environment is no longer needed, delete the node group and
then the EKS cluster after preserving any required evidence. Delete the project
launch template and ECR repository only when they are no longer needed. Leave
the account's default VPC and subnets in place.
