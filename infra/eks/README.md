# Learner Lab EKS deployment

Region: `us-east-1`. Cluster: `cloudproject-sandbox-lab`. The cluster uses
Kubernetes 1.34, the lab's pre-created EKS cluster and node roles, and two
default-VPC subnets. Its public API endpoint is limited to the operator's current
public IP `/32`; private endpoint access is also enabled. Do not commit the
operator's current IP or AWS credentials.

The sandbox image is in the private `cloudproject-sandbox` ECR repository at tag
`20261024-runner1`. Resolve its digest before creating Jobs:

```sh
aws ecr describe-images --region us-east-1 \
  --repository-name cloudproject-sandbox \
  --image-ids imageTag=20261024-runner1 \
  --query 'imageDetails[0].imageDigest' --output text
```

## Check the live cluster

```sh
aws eks describe-cluster --region us-east-1 \
  --name cloudproject-sandbox-lab \
  --query 'cluster.{status:status,version:version,endpoint:resourcesVpcConfig}'
aws eks list-nodegroups --region us-east-1 \
  --cluster-name cloudproject-sandbox-lab
```

The node launch template is `cloudproject-eks-nodes`. It requires IMDSv2 and
sets the metadata response hop limit to one. The active managed node group uses
one `t3.medium` node initially and a maximum of two. The node group limit alone
does not provide demand-driven scaling. The operator chose to leave Cluster
Autoscaler off. `autoscaler-node-policy.json` is a proposal and has **not**
been attached to the shared node role.

The Learner Lab identity cannot create a dedicated gateway IAM role. Its
cluster-creator identity is being used for initial setup and smoke tests. Do not
claim namespace-scoped gateway authorization until a separate role and EKS access
entry are available. The RBAC manifest in `infra/k8s/gateway-rbac.yaml` is
prepared but cannot enforce a limit on that cluster-admin identity.

For an explanation of which components run locally, what AWS hosts, and how
teammates can use this code, see [the team handoff](../../docs/eks-team-handoff.md).

## Run the gateway on this Mac

Keep current Learner Lab AWS credentials in `~/.aws/credentials`, outside the
repository. Set these values in the gitignored `.env`:

```dotenv
LOCAL_MEMORY_BUDGET_MB=256
EKS_ENABLED=true
EKS_CONTEXT=cloudproject-sandbox-lab
EKS_IMAGE=257465915306.dkr.ecr.us-east-1.amazonaws.com/cloudproject-sandbox@sha256:b83af745ca35080f205abcb1fe2a898973ec1d02817bc08e75733cbfda856230
EKS_MAX_INFLIGHT=3
```

Start Docker Desktop, then run `.venv/bin/python -m src.dashboard` from the
project directory and open `http://127.0.0.1:8000`. Submit with
`.venv/bin/python -m src.sender.send src/examples/good.py`. Cloud work returns
HTTP 202 first; the CLI polls for the final status. To reliably demonstrate
overflow, temporarily start the dashboard with
`LOCAL_MEMORY_BUDGET_MB=1 .venv/bin/python -m src.dashboard`, then restore
the ordinary 256 MB budget on the next start.

The cluster API allowlist contains the operator's public IP at creation time.
If the Mac's public IP changes, update EKS public access CIDRs before running
the gateway. The `voclabs` credentials expire and must be refreshed from the
Learner Lab when authentication fails.

## Cleanup

The EKS control plane incurs hourly charges while it exists, even when the
node group is empty. After preserving test evidence, delete the managed node
group, wait for deletion, then delete the cluster. The default VPC and its
subnets predate this project and must remain. Remove the project launch template
and ECR repository only when the project no longer needs them. Verify each
resource's absence with the corresponding AWS `describe` or `list` command.
