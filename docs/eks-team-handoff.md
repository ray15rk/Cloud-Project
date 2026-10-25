# EKS team handoff

## What is running

This project is a **hybrid local/cloud demonstration**. On 24 October 2026,
the EKS cluster `cloudproject-sandbox-lab` in `us-east-1` and its one-node
`sandbox-workers` managed node group were active in one Learner Lab AWS account.
The published sandbox image is private in that account's ECR repository.

```text
Browser / sender CLI
       |
       v
Local gateway on 127.0.0.1:8000 -- HMAC verification
       |
       +-- local slot available --> Docker dry run on this computer
       |
       +-- local slots full -----> EKS Job dry run in AWS
                                      |
                                      v
                               result/status returned
       |
       v
Local endpoint on 127.0.0.1:5000 -- executes successful payload
```

The dashboard (`127.0.0.1:8000`) and central notification server
(`127.0.0.1:9000`) also run on the computer that starts `src.dashboard`.
There is no cloud-hosted gateway, public application URL, Kubernetes Service,
or Ingress. When that computer is off, no one can use its dashboard or endpoint.
The EKS cluster and worker remain provisioned in AWS until explicitly deleted.

The gateway admits two 128 MB local dry runs with the configured 256 MB local
budget. A verified overflow request receives HTTP 202 and a status URL. A
durable, capped local SQLite queue submits a disposable EKS Job, then forwards
the **original verified bytes** to the local endpoint only after the dry run
succeeds. The sender CLI and dashboard poll for the final result. The default
cap is three outstanding cloud submissions. The namespace also has a Kubernetes
ResourceQuota. An optional local node scaler watches resource-pending sandbox
Pods and adjusts the node group between one and two nodes. It stops when the
gateway stops or AWS lab credentials expire. Kubernetes Cluster Autoscaler is
unavailable because the lab denies the dedicated IAM role it needs.

## What was proved

- A signed example completed in EKS and was delivered to the local endpoint.
- An infinite loop hit the EKS execution deadline and was blocked before
  endpoint delivery.
- A four-probe capacity test put two dry runs in local Docker and two in EKS;
  all four successful scripts were delivered. Job names and timing data are in
  the dashboard activity journal.
- A separate three-submission signed probe put exactly two dry runs in local
  Docker and one in EKS; all three reached the local endpoint.
- With both local slots occupied, an EKS infinite-loop dry run timed out and
  was blocked before forwarding (HTTP 422), while both local runs succeeded.
- A six-submission capacity probe produced two local successes, three EKS
  successes, and one HTTP 503 capacity rejection. This confirms the configured
  two local slots and three outstanding cloud submissions are enforced.
- A resource scheduling probe requested a second node: the second worker
  reached Ready, and a pending Pod moved onto it. A third probe remained
  pending at the two-node cap. The labeled probe Pods were deleted afterward,
  and the operator-side scaler requested the return to one desired node.
- Kubernetes Jobs, Pods, and payload ConfigMaps created for these tests were
  cleaned up. The full repository test suite passed: 92 tests after the latest
  Nginx changes were merged.

These results prove the configured environment worked at test time. They do
not make the application production ready. The endpoint executes scripts on
its host after the dry run. Only the dry run is sandboxed. The current Learner
Lab permissions also force the gateway to use the cluster creator's broad
identity; the prepared namespace RBAC does not limit that identity.

## How a teammate can run it

### Local-only mode on their own computer

1. Clone the repository, install Python 3.11+, Docker Desktop, and
   `requirements.txt`. Build `cloudproject-sandbox:dev` from `docker/sandbox`.
2. Copy `env.example` to a gitignored `.env`, generate their own
   `SHARED_SECRET`, and leave `EKS_ENABLED=false`.
3. Run `python -m src.dashboard` from the repository root and open
   `http://127.0.0.1:8000` **on that same computer**.

This needs no AWS account. Once local slots are full, the gateway returns a
capacity error rather than using EKS.

### Use the existing EKS cluster from another computer

This works only if the teammate has an authorized IAM identity in the **same
AWS account** and Kubernetes access to this cluster. They also need the EKS
public API endpoint to allow their computer's current public IP. Cloning the
repository or copying `~/.kube/config` does not grant either permission.
Do not copy another person's temporary AWS credentials or shared secret.

With those permissions granted by the account/lab administrator, the teammate
can configure their own AWS CLI credentials, run
`aws eks update-kubeconfig --region us-east-1 --name cloudproject-sandbox-lab`,
and check `kubectl get nodes`. They need Docker locally for local dry runs,
the same ECR image digest in their `.env`, and their own gateway/endpoint
processes. The ECR repository is private; the **EKS worker** pulls its image
using its node role. See [AWS's kubeconfig guide](https://docs.aws.amazon.com/eks/latest/userguide/create-kubeconfig.html)
and [EKS access entries](https://docs.aws.amazon.com/eks/latest/userguide/access-entries.html).

The current Learner Lab denied creation of a dedicated gateway IAM role. If
the lab cannot grant a teammate's identity appropriately, this shared-cluster
option is not ready for them. Ask the instructor/lab administrator for
individual access rather than sharing credentials.

### Use a different Learner Lab AWS account

A different account cannot automatically use this private ECR image or this
cluster. Follow the [macOS/Linux setup](eks-teammate-setup.md) or
[Windows PowerShell setup](eks-teammate-setup-windows.md) to
build/push the sandbox image in **your own account**, create the bounded EKS
cluster and node group, enable network isolation, configure `.env`, run an
overflow test, and clean up. It uses commands rather than a one-command
provisioning script because IAM role names, subnets, public IP ranges, and
image digests differ by account. Do not copy Rance's values.

## Sharing and operating the project

Push source code, Kubernetes manifests, `env.example`, and documentation.
Never push `.env`, `~/.aws/credentials`, `~/.kube/config`, `.data/`, or a public
IP allowlist. The first two repository paths are gitignored; the home-directory
files are outside the repository. Do not put temporary lab keys in chat or a
GitHub issue.

The EKS control plane and EC2 worker consume lab credit while provisioned,
even if the local dashboard is stopped. AWS lists the cluster charge and
worker-resource charges separately in its [EKS FAQ](https://aws.amazon.com/eks/faqs/).
Track the lab balance and decide who owns cleanup. Follow
[`infra/eks/README.md`](../infra/eks/README.md) to inspect and remove the
project resources when the demonstration is over. Refresh temporary AWS
credentials when they expire; update the EKS API allowlist if the authorized
operator's public IP changes.
