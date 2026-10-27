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

Use the AWS CLI profile created for this account:

```sh
aws eks describe-cluster --profile cloudproject-sydney \
  --region ap-southeast-2 --name cloudproject-sandbox-sydney \
  --query 'cluster.{status:status,version:version}'

aws eks list-nodegroups --profile cloudproject-sydney \
  --region ap-southeast-2 --cluster-name cloudproject-sandbox-sydney

KUBECONFIG=/Users/rance/.kube/config-cloudproject-sydney \
  kubectl --context cloudproject-sandbox-sydney get nodes -L workload
helm list -n kube-system
```

Set `KUBECONFIG=/Users/rance/.kube/config-cloudproject-sydney` in the local
environment or `.env` before using the Sydney Kubernetes context.

## Sandbox image

The sandbox image is stored in the Sydney ECR repository. Use the image's
immutable digest rather than a mutable tag:

```text
562358727339.dkr.ecr.ap-southeast-2.amazonaws.com/cloudproject-sandbox@sha256:ea1f0713883b312c27f9465700f049e469d4627ac96304a4cb7fd4f9e711cf01
```

This is the digest used for the initial successful smoke test. Update
`EKS_IMAGE` when a new image is built and pushed.

## Run the gateway on this Mac

Keep AWS credentials outside the repository. Set these values in the gitignored
`.env`, alongside the generated `SHARED_SECRET` and existing local service URLs:

```dotenv
LOCAL_MEMORY_BUDGET_MB=256
EKS_ENABLED=true
EKS_CONTEXT=cloudproject-sandbox-sydney
EKS_IMAGE=562358727339.dkr.ecr.ap-southeast-2.amazonaws.com/cloudproject-sandbox@sha256:ea1f0713883b312c27f9465700f049e469d4627ac96304a4cb7fd4f9e711cf01
EKS_MAX_INFLIGHT=3
AWS_REGION=ap-southeast-2
AWS_PROFILE=cloudproject-sydney
KUBECONFIG=/Users/rance/.kube/config-cloudproject-sydney
GATEWAY_URL=http://127.0.0.1:8080/submit
```

From the project directory, start the dashboard:

```sh
.venv/bin/python -m src.dashboard
```

Open `http://127.0.0.1:8000`. With the project's Nginx proxy running on port
8080, submit an example from another terminal:

```sh
.venv/bin/python -m src.sender.send src/examples/good.py
```

To demonstrate cloud bursting, restart the dashboard with a temporary low local
memory budget:

```sh
LOCAL_MEMORY_BUDGET_MB=1 .venv/bin/python -m src.dashboard
```

The submission should first return HTTP 202 while the EKS Job is queued, then
return a completed result with `execution_location: eks`. Restart the dashboard
normally afterward to restore the configured local memory budget.

The gateway's cloud path and Cluster Autoscaler's node scaling are separate:
the gateway chooses EKS when local capacity is insufficient; Cluster Autoscaler
adds a worker node only when Kubernetes cannot schedule a Pod on existing nodes.

## Verified behavior

- A sandbox Job completed and printed `RUNTIME_OK`.
- The sandbox Job's attempted outbound network connection was blocked.
- A pending workload caused Cluster Autoscaler to add a second worker node.
- After the workload was deleted, Cluster Autoscaler removed the extra node.
- A gateway submission completed successfully through EKS and returned
  `execution_location: eks`.

## Costs and shutdown

The EKS control plane, worker nodes, storage, and other used AWS resources can
incur charges while they exist. Scaling back to one node does not stop all
charges. When the environment is no longer needed, delete the node group and
then the EKS cluster after preserving any required evidence. Delete the project
launch template and ECR repository only when they are no longer needed. Leave
the account's default VPC and subnets in place.
