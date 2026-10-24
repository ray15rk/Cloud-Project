# EKS implementation plan

Implement cloud overflow for the existing verified-script pipeline. Keep the gateway, dashboard, central server, and endpoint local for the first milestone; run overflow dry runs as disposable EKS Jobs. Provision the cluster ahead of demonstrations. Create Jobs per submission, not clusters.

The endpoint currently executes the forwarded script directly on its host. An isolated EKS dry run does not sandbox that later execution. Treat the endpoint as a trusted demonstration component until it is independently isolated; do not claim end-to-end execution isolation.

This plan was drafted from repository and AWS/Kubernetes documentation on 22 October 2026. The deployment status below records what was verified on 24 October 2026.

## Deployment status, 24 October 2026

- ECR image, EKS 1.34 cluster, one `t3.medium` managed worker, strict VPC CNI network policy, restricted sandbox namespace, and disposable Jobs are operational in `us-east-1`.
- The gateway now routes verified local overflow into a bounded SQLite queue, returns HTTP 202, and exposes `/submissions/<id>`. The CLI and dashboard poll final status. A live signed submission completed through EKS and reached the endpoint; a live infinite loop was blocked with HTTP 422. All project Jobs, Pods, and payload ConfigMaps were removed after those tests.
- Node autoscaling is intentionally off at the operator's request. The node group has a maximum of two but remains at one until manually resized.
- The Learner Lab denies creating a dedicated gateway IAM role. The current cluster creator identity has broad permissions, so the namespace-scoped RBAC manifest cannot currently constrain the gateway identity. Resolve this before calling the deployment least-privileged.
- EKS does not reproduce the local Docker process count limit; the image has resource, filesystem, deadline, and network controls. The endpoint still executes delivered scripts directly on the local host.
- The lab supplied a $50 budget figure but no service restriction document or remaining balance. The running control plane and EC2 node consume budget until cleaned up.

## 1. Verify the lab prerequisites

- Confirm the permitted region, remaining lab budget, EKS availability, allowed EC2 instance types, and available IAM roles in the lab instructions.
- Install AWS CLI v2, kubectl, and the chosen provisioning tool. Use eksctl configuration committed to the repo if the lab permits it; otherwise document reproducible console steps using the provided roles.
- Refresh temporary AWS credentials outside the repo. Run `aws sts get-caller-identity`, `aws eks list-clusters`, and `aws ecr describe-repositories` with the selected profile and region. The existing PowerShell script performs these read checks.
- Separately establish permission to create ECR/EKS/EC2 networking resources, pass the required roles, configure cluster access, and operate node autoscaling. Listing clusters does not establish these permissions.
- Identify separate cluster, node, autoscaler, and gateway permissions. Arrange the EC2 Auto Scaling service-linked role during deployment if required and permitted. If lab restrictions prevent a required component, resolve that with the instructor before provisioning.

Done when: the team has a recorded region, role mapping, allowed instance type, provisioning method, and cleanup owner.

## 2. Publish the sandbox image to ECR

- Create a private ECR repository for the sandbox.
- Build from `docker/sandbox`. Match the image architecture to the EKS nodes; an Apple Silicon local build must not accidentally produce an ARM-only image for x86 nodes.
- Authenticate Docker to ECR, tag, and push the image. Use a versioned tag and deploy its immutable digest.
- Add an in-container runner that enforces the existing 10-second payload execution limit and bounds captured output. Rebuild and publish it before the cloud integration test. The current image runs Python directly and relies on the local Docker client for its deadline.
- Keep scripts, HMAC keys, and AWS credentials out of the image.

Done when: a test EKS Pod can pull and execute the correct image. [ECR push guide](https://docs.aws.amazon.com/AmazonECR/latest/userguide/docker-push-ecr-image.html)

## 3. Provision a small EKS environment

- Use an EKS managed node group with EC2 Linux nodes. Start with one ready node and a proposed maximum of two, subject to lab limits and system resource needs.
- Choose a supported Kubernetes version and compatible add-ons when provisioning; record their versions.
- Configure VPC/subnets across the required Availability Zones and explicit worker access to ECR and its dependencies through the chosen NAT or VPC endpoint arrangement.
- Enable private API access and restrict public API access to the team's current public IP ranges, so the local gateway can reach the cluster. Update these ranges when networks change.
- Configure an EKS access entry and namespace-scoped Kubernetes RBAC for the gateway. Permit managing its Jobs and payload ConfigMaps and reading Pod status/logs; avoid using cluster-admin for the application.
- Create a dedicated `cloudproject-sandbox` namespace with resource quotas and Pod Security admission configured for restricted workloads.

Done when: nodes are Ready, the gateway identity can perform required namespace operations, and unrelated namespaces are inaccessible. [EKS endpoint configuration](https://docs.aws.amazon.com/eks/latest/userguide/cluster-endpoint.html)

## 4. Build and prove the cloud sandbox

Create a Job template and namespace policies before wiring it into the gateway:

| Setting | Initial proposal |
| --- | --- |
| CPU request and limit | `500m`, matching the local 0.5 CPU limit |
| Memory request and limit | `128Mi`, matching the local 128m allocation |
| Execution | One completion, one parallel Pod, `restartPolicy: Never`, `backoffLimit: 0` |
| Script deadline | 10 seconds, enforced inside the container |
| Total Job deadline | Start with 300 seconds including scheduling/image pull; measure and tune |
| Finished Job TTL | 300 seconds as fallback cleanup |
| Identity | UID/GID 65534; `runAsNonRoot: true` |
| Filesystem/privileges | Read-only root, no privilege escalation, drop all capabilities, RuntimeDefault seccomp |
| Credentials/host access | No service-account token, host namespaces, host mounts, or Docker socket |
| Network | Deny all sandbox ingress and egress |

- Enable VPC CNI network policy enforcement in strict mode before executing payloads. Configure necessary system namespace allowances separately. Merely applying NetworkPolicy YAML is insufficient.
- Verify that scripts cannot reach other Pods, the public internet, Kubernetes API, or EC2 instance metadata. Configure node metadata protections as needed; do not assume network policy covers every host-network path.
- The local Docker runner also sets a 64-process limit. Standard Pod YAML has no direct equivalent to Docker's `--pids-limit`; configure and test an appropriate kubelet `podPidsLimit` through supported node configuration, or explicitly record the parity gap.
- Supply verified bytes through a per-submission immutable ConfigMap using `binaryData`, mounted read-only at `/sandbox/script.py`. Preserve the existing 64 KB input cap and record a payload hash. No S3 access or payload network access is needed.
- Read actual container termination state to distinguish OOMKilled, execution failure, and infrastructure failure. Do not infer every exit code 137 is an OOM.
- Collect the result before deleting the Job and ConfigMap. Give the ConfigMap a Job owner reference once possible; reconcile orphaned objects after partial creation or gateway restart. TTL cleanup alone does not remove unrelated ConfigMaps.

Done when: good code completes; loops, excessive memory, network access, and prohibited writes fail; objects are cleaned up.

References: [Job controls](https://kubernetes.io/docs/concepts/workloads/controllers/job/), [strict network policy enforcement](https://docs.aws.amazon.com/eks/latest/userguide/cni-network-policy-configure.html).

## 5. Add the EKS runner and routing

- Add `src/sandbox/eks_runner.py` using the Kubernetes Python client and refreshable kubeconfig/AWS authentication. Never place AWS credentials in payload Pods.
- Give local and EKS runners a common result contract: status, detail, bounded output, execution location, submission ID, and timing fields.
- Retain HMAC verification before either runner. In `gateway_app.py`, use Docker if `try_admit()` succeeds; otherwise submit an EKS Job when cloud execution is enabled. When disabled or unavailable, retain fail-closed rejection and notifications.
- Release local reservations only for locally admitted runs. Keep local and cloud accounting separate.
- Make the local memory budget configurable. Set it to 256 MB for the two-local-plus-one-cloud demonstration; the existing 512 MB default allows four 128 MB reservations.
- Add a bounded cloud admission count and namespace quota. Reject further submissions with a clear capacity result when the cap is reached; do not create an unlimited queue of Jobs.
- Use durable asynchronous submission tracking for cloud work: return HTTP 202 with an ID, process it with a worker, and expose a status endpoint. Update the CLI and dashboard to poll until terminal status. Cloud scheduling can outlast the current CLI's 60-second HTTP timeout.
- Persist submission state separately from the bounded activity history. Recover in-flight Jobs after gateway restart; expire old submissions and clean up their payloads. Store any payload needed for later forwarding only temporarily with restricted access.
- Forward the original verified bytes only after confirmed sandbox success. An uncertain or failed Job must never trigger forwarding.

Done when: an overflow submission travels from gateway to EKS and back to the local endpoint with a visible final result.

## 6. Handle retries and add node autoscaling

- First prove the cloud path using the ready node. Then install Cluster Autoscaler with a version matching the cluster and permissions limited to its node group.
- Configure node group auto-discovery and bounded minimum/maximum size. A managed node group's maximum size alone does not implement demand-driven scaling.
- Retry transient infrastructure operations with exponential backoff, jitter, and a fixed deadline. Refresh expired authentication where possible; report persistent authorization errors instead of retrying indefinitely.
- Use a stable Job name derived from the durable submission ID. If Job creation times out, query that name before trying again to avoid duplicate Jobs.
- Never retry a script because it failed or timed out. `backoffLimit: 0` disables ordinary Job failure retries, but do not promise exactly-once execution under all distributed failures. Guard endpoint forwarding with persisted state; mark ambiguous forwarding outcomes for investigation rather than blindly resend.
- IAM readiness checks and bounded retries handle propagation delay; they cannot guarantee that every AWS permission has propagated.

Done when: unschedulable test work triggers a node increase within the configured cap, and transient API failures do not create duplicate submissions. [Cluster Autoscaler guidance](https://docs.aws.amazon.com/eks/latest/best-practices/cas.html)

## 7. Update visibility and validate the complete pipeline

Extend the dashboard and activity records with execution location, Job name, queue/startup/execution timing, cloud health, and failure reason. Replace the hardcoded EKS-disconnected status with a real check. Keep scripts and credentials out of logs/history.

| Scenario | Required evidence |
| --- | --- |
| Valid local submission | Docker success followed by endpoint delivery |
| Tampered or replayed submission | Rejected before any cloud Job exists |
| Two local slots occupied, third valid submission | Two local runs and one real EKS run; use overlapping scripts to make the result deterministic |
| Loop or memory exhaustion in EKS | Terminated, notification recorded, no endpoint delivery |
| EKS unavailable or permission denied | Bounded failure and notification, no delivery |
| Lost Job-create response | Reconciled by submission ID without a new Job name |
| Gateway restart during cloud run | Durable tracking recovers without duplicate forwarding |
| Cloud admission cap reached | Clear capacity rejection; object count remains bounded |
| Node autoscaling | Separate test exceeds node allocatable requests and proves node count increases; one overflow script may not trigger scaling |
| Cleanup | No leftover Jobs, Pods, or payload ConfigMaps after completion/failure |

Use mocked Kubernetes API tests for routing and recovery, then a small real EKS integration suite. Capture Job/Pod status, node counts, endpoint results, notifications, and timings for the report.

## 8. Commit reproducible setup and cleanup

Suggested implementation files:

- `infra/eks/cluster.yaml`: cluster/node configuration and version choices.
- `infra/k8s/`: namespace, RBAC, quota, network policy, and Job template.
- `src/sandbox/eks_runner.py` and an in-container deadline runner.
- A durable submission worker/store and status API, integrated with `src/gateway/`.
- Updates to `src/resource_manager/resource_manager.py`, CLI, dashboard, and `env.example`.
- `src/tests/test_eks_runner.py` plus routing/recovery tests.
- Deployment and cleanup scripts with matching instructions in `docs/aws-setup.md`.

At the end of demonstrations, delete workload objects and unneeded node groups, cluster, ECR images, and owned networking resources according to lab rules. Confirm removal in AWS; do not assume stopping a lab session removes resources. Preserve test evidence before cleanup.

Implementation order: prerequisites → image → cluster → isolated Job → gateway integration → autoscaling/recovery → end-to-end evidence → cleanup.
