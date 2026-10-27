# EKS implementation status and remaining work

The project uses a local gateway for signed submissions and a Sydney EKS
cluster for overflow dry runs. The gateway chooses local Docker or an EKS Job;
Kubernetes Cluster Autoscaler independently changes worker node capacity.
See [deployment details](../infra/eks/README.md) for current resource names and
the [team handoff](eks-team-handoff.md) for the request path.

## Verified deployment (27 October 2026)

- EKS `cloudproject-sandbox-sydney` runs Kubernetes 1.36 in `ap-southeast-2`
  with a managed `t3.small` node group bounded at one to two nodes.
- The private ECR sandbox image is deployed by immutable digest. The Job
  template uses resource limits, a non-root identity, a read-only root
  filesystem, no service-account token, no privilege escalation, and a
  per-script deadline.
- The `cloudproject-sandbox` namespace has a resource quota and restricted
  Pod Security settings. VPC CNI enforces network policy in strict mode.
  A smoke Job completed and an attempted outbound connection was blocked.
- Cluster Autoscaler uses a dedicated Pod Identity role. A resource-pending
  workload caused a second worker to become Ready; after workload removal,
  the extra worker was removed.
- A normal gateway submission completed locally. With a temporary low local
  memory budget, an overflow submission returned HTTP 202 and later
  completed through EKS with `execution_location: eks`.
- After removing the old gateway-side node scaler, 89 project tests passed.
  The local and EKS submission paths were exercised again.

These results describe the tested configuration, not a production guarantee.
The gateway and final endpoint still run on the operator's computer. The
endpoint executes a successful payload on that host after the sandbox dry run;
the endpoint execution itself is not isolated by EKS.

## Remaining engineering work

- Establish and verify a dedicated, least-privilege AWS identity and
  namespace-scoped Kubernetes access for the gateway. The current operator
  profile's access has not been demonstrated to be limited to the sandbox
  namespace. `infra/k8s/gateway-rbac.yaml` is a prepared manifest, not proof
  that the gateway uses those permissions.
- Validate the sandbox's isolation against other Pods, the Kubernetes API,
  instance metadata, and all intended network paths. The observed outbound
  block is one smoke test. Document any difference from the local Docker PID
  limit.
- Test gateway recovery after interruption, ambiguous Job creation, cleanup
  of orphaned ConfigMaps, authorization failure, overload, and endpoint
  delivery uncertainty before claiming production resilience.
- Record reproducible provisioning for a fresh account, including IAM roles,
  EKS access, VPC choices, ECR, Pod Identity, Helm chart and autoscaler image
  versions, add-on versions, and cleanup. The current teammate guides cover
  authorized use of the existing Sydney cluster.
- Capture costs and an explicit shutdown owner. Stopping the local dashboard
  does not remove the EKS control plane or worker charges.

## Recreating the deployment in another account

A separate account cannot use the Sydney cluster or private ECR repository
without explicit cross-account setup. Plan these resources in order:

1. Confirm account, region, service availability, cost budget, and IAM
   permissions. Create cluster, node, autoscaler, and gateway identities with
   distinct responsibilities.
2. Create a private ECR repository, build the sandbox image for the worker
   architecture, push it, and record its immutable digest.
3. Create EKS across suitable subnets with deliberate API endpoint access.
   Create a managed node group with the sandbox label and a bounded size.
4. Apply the sandbox namespace and CoreDNS policies. Enable VPC CNI network
   policy enforcement in strict mode and verify the add-on is healthy.
5. Tag the node group's Auto Scaling group for Cluster Autoscaler discovery.
   Install a compatible autoscaler with a dedicated Pod Identity association
   and policy scoped to the new cluster's ASG tags.
6. Run a sandbox Job smoke test, a node scale-up/scale-down test, then the
   gateway's local and EKS submission tests. Review costs and cleanup.

The checked-in manifests under `infra/k8s/` and policy examples under
`infra/eks/` are inputs to this process; they do not provision a cluster by
themselves. Replace account-specific names, ARNs, paths, tags, and image
digests for the new deployment.
