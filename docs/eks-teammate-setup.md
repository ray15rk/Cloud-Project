# Set up EKS in your own Learner Lab account

Use this guide from the repository root on macOS or Linux. It recreates the
project's private ECR image, EKS cluster, one-to-two-node managed node group,
sandbox namespace, and network isolation in **your own** AWS account. It does
not give you access to Rance's separate Learner Lab account. The gateway,
dashboard, central server, and final endpoint still run on your computer.
For Windows PowerShell, follow the [Windows setup guide](eks-teammate-setup-windows.md).

The commands below create billable EKS and EC2 resources. Check your lab's
remaining credit and allowed services/instance types first. Our lab allowed
EKS 1.34 and `t3.medium`; yours may differ. If the lab denies an IAM role,
`iam:PassRole`, EKS, EC2, or ECR, stop at that step and ask the instructor.
Do not use another teammate's credentials or attach broad permissions to a
shared node role to work around a denial.

## 1. Prepare your computer and lab account

Install AWS CLI v2, `kubectl`, Docker Desktop, Python 3.11 or newer, and Nginx.
Clone
the `rance` branch and install the project's Python requirements:

```sh
git clone -b rance https://github.com/ray15rk/Cloud-Project.git
cd Cloud-Project
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Start Learner Lab and save **your own** current `[default]` AWS CLI credentials
in `~/.aws/credentials`, following [AWS setup](aws-setup.md). Session tokens
expire; refresh the file when AWS authentication fails. Keep credentials and
your public IP out of Git and chat.

```sh
REGION=us-east-1
CLUSTER=cloudproject-sandbox-lab
NODEGROUP=sandbox-workers
REPOSITORY=cloudproject-sandbox
IMAGE_TAG=20261024-runner1
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
aws sts get-caller-identity --query '{Account:Account,Arn:Arn}' --output json
```

Confirm the displayed account is **yours** before creating anything. Keep
this terminal open: later shell variables depend on it. Run the read-only AWS
checks in [AWS setup](aws-setup.md) if this is a new lab session.

## 2. Identify the lab roles, VPC, and subnets

Our lab supplied roles whose names contained `LabEksClusterRole` and
`LabEksNodeRole`. List the roles in your own account; their full names and ARNs
will differ:

```sh
aws iam list-roles --query "Roles[?contains(RoleName, 'LabEksClusterRole')].[RoleName,Arn]" --output table
aws iam list-roles --query "Roles[?contains(RoleName, 'LabEksNodeRole')].[RoleName,Arn]" --output table
```

Copy the matching ARNs into local shell variables. The cluster role must trust
`eks.amazonaws.com`; the node role must trust `ec2.amazonaws.com` and permit
EKS nodes to join the cluster and pull from ECR. If either role is missing or
the lab denies passing it, ask the instructor for an allowed role. Do not copy
the ARNs from [Rance's deployment notes](../infra/eks/README.md).

```sh
CLUSTER_ROLE_ARN=REPLACE_WITH_YOUR_CLUSTER_ROLE_ARN
NODE_ROLE_ARN=REPLACE_WITH_YOUR_NODE_ROLE_ARN
VPC_ID=$(aws ec2 describe-vpcs --region "$REGION" --query 'Vpcs[?IsDefault].VpcId | [0]' --output text)
aws ec2 describe-subnets --region "$REGION" \
  --filters "Name=vpc-id,Values=$VPC_ID" "Name=default-for-az,Values=true" \
  --query 'Subnets[].{ID:SubnetId,AZ:AvailabilityZone,PublicIP:MapPublicIpOnLaunch}' \
  --output table
```

Choose **two different Availability Zones** from that output, with
`PublicIP=True`, and set their IDs below. Stop if there is no default VPC or
no suitable subnets; private subnets need different routing/NAT or VPC
endpoints. Find your computer's current public IPv4 using a service you trust
and enter it locally. The `/32` limits access to the EKS public API endpoint;
private endpoint access lets nodes use the API from inside the VPC.

```sh
SUBNET_A=REPLACE_WITH_FIRST_SUBNET_ID
SUBNET_B=REPLACE_WITH_SECOND_SUBNET_ID
PUBLIC_IPV4=REPLACE_WITH_YOUR_PUBLIC_IPV4
```

## 3. Build and push your sandbox image

Build an x86-64 image even if your Mac uses Apple Silicon, because this guide
uses x86-64 `t3.medium` workers. The ECR repository is private to your account.

```sh
docker info --format '{{.OSType}}'
docker build --platform linux/amd64 -t cloudproject-sandbox:dev docker/sandbox
aws ecr create-repository --region "$REGION" --repository-name "$REPOSITORY"
ECR_REGISTRY="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
ECR_URI="$ECR_REGISTRY/$REPOSITORY"
aws ecr get-login-password --region "$REGION" | \
  docker login --username AWS --password-stdin "$ECR_REGISTRY"
docker tag cloudproject-sandbox:dev "$ECR_URI:$IMAGE_TAG"
docker push "$ECR_URI:$IMAGE_TAG"
IMAGE_DIGEST=$(aws ecr describe-images --region "$REGION" \
  --repository-name "$REPOSITORY" --image-ids "imageTag=$IMAGE_TAG" \
  --query 'imageDetails[0].imageDigest' --output text)
EKS_IMAGE="$ECR_URI@$IMAGE_DIGEST"
echo "$EKS_IMAGE"
```

Check that the final URI ends in `@sha256:...`. If the repository already
exists, skip `create-repository`. If the push is denied, verify this account's
ECR permissions and region. Do not use another account's private ECR URI.

## 4. Create the EKS cluster and bounded worker group

Set the API allowlist using **your** public IP. EKS 1.34 was the version used
for our tested deployment; if your lab cannot create it, choose a version it
supports and use compatible add-ons. The project code does not require exactly
1.34, but any later Kubernetes Cluster Autoscaler version must match the
cluster minor version.

```sh
aws eks create-cluster --region "$REGION" --name "$CLUSTER" --version 1.34 \
  --role-arn "$CLUSTER_ROLE_ARN" \
  --resources-vpc-config \
  "subnetIds=$SUBNET_A,$SUBNET_B,endpointPublicAccess=true,endpointPrivateAccess=true,publicAccessCidrs=$PUBLIC_IPV4/32"
aws eks wait cluster-active --region "$REGION" --name "$CLUSTER"
aws eks update-kubeconfig --region "$REGION" --name "$CLUSTER" --alias "$CLUSTER"
kubectl --context "$CLUSTER" get namespaces
```

The launch template makes EC2 instance metadata require IMDSv2 and limits
the metadata response hop count. Do not put an AMI ID or instance type into
this template; EKS selects the compatible node AMI and the node group sets
the instance type.

```sh
LAUNCH_TEMPLATE=cloudproject-eks-nodes
aws ec2 create-launch-template --region "$REGION" \
  --launch-template-name "$LAUNCH_TEMPLATE" \
  --launch-template-data file://infra/eks/launch-template-data.json
aws eks create-nodegroup --region "$REGION" --cluster-name "$CLUSTER" \
  --nodegroup-name "$NODEGROUP" --node-role "$NODE_ROLE_ARN" \
  --subnets "$SUBNET_A" "$SUBNET_B" --instance-types t3.medium \
  --ami-type AL2023_x86_64_STANDARD --capacity-type ON_DEMAND \
  --scaling-config minSize=1,maxSize=2,desiredSize=1 \
  --labels workload=cloudproject-sandbox \
  --launch-template "name=$LAUNCH_TEMPLATE,version=1"
aws eks wait nodegroup-active --region "$REGION" \
  --cluster-name "$CLUSTER" --nodegroup-name "$NODEGROUP"
kubectl --context "$CLUSTER" get nodes -L workload
```

Wait for one `Ready` node labeled `workload=cloudproject-sandbox`. The
maximum of two is only a limit; it does not by itself scale nodes on demand.
If node creation fails, inspect `aws eks describe-nodegroup` health issues,
role policies, subnet public IP settings, and the lab's EC2 restrictions.

## 5. Enable and check network isolation

The sandbox Job's Kubernetes NetworkPolicy is meaningful only when a network
policy engine enforces it. This deployment uses the managed Amazon VPC CNI
add-on. First enable policy support in its default mode. If `vpc-cni` is
already an EKS managed add-on, use `update-addon`; otherwise use
`create-addon` (a fresh CLI-created cluster may have a self-managed CNI).
Check which case you have, then run **one** of these commands:

```sh
aws eks list-addons --region "$REGION" --cluster-name "$CLUSTER" --output table
```

```sh
aws eks create-addon --region "$REGION" --cluster-name "$CLUSTER" \
  --addon-name vpc-cni --resolve-conflicts OVERWRITE \
  --configuration-values file://infra/eks/vpc-cni-policy.json
```

```sh
aws eks update-addon --region "$REGION" --cluster-name "$CLUSTER" \
  --addon-name vpc-cni \
  --configuration-values file://infra/eks/vpc-cni-policy.json
```

Wait for the managed add-on to be `ACTIVE`, then apply the CoreDNS allowance
and the restricted sandbox namespace. CoreDNS needs a policy before switching
the VPC CNI to strict startup mode.

```sh
aws eks wait addon-active --region "$REGION" --cluster-name "$CLUSTER" --addon-name vpc-cni
kubectl --context "$CLUSTER" apply -f infra/k8s/coredns-networkpolicy.yaml
kubectl --context "$CLUSTER" apply -f infra/k8s/sandbox-namespace.yaml
aws eks update-addon --region "$REGION" --cluster-name "$CLUSTER" \
  --addon-name vpc-cni \
  --configuration-values file://infra/eks/vpc-cni-policy-strict.json
aws eks wait addon-active --region "$REGION" --cluster-name "$CLUSTER" --addon-name vpc-cni
kubectl --context "$CLUSTER" get networkpolicy -A
kubectl --context "$CLUSTER" -n cloudproject-sandbox get resourcequota
kubectl --context "$CLUSTER" -n kube-system get ds aws-node \
  -o jsonpath='{.spec.template.spec.containers[*].name}'
kubectl --context "$CLUSTER" -n kube-system get ds aws-node \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="aws-node")].env[?(@.name=="NETWORK_POLICY_ENFORCING_MODE")].value}'
```

The last two commands should include `aws-network-policy-agent` and print
`strict`, respectively. If they do not, stop before running payloads. If the add-on
fails, inspect its `describe-addon` health issues and the version's
configuration schema before running payloads. See [AWS's VPC CNI policy
guide](https://docs.aws.amazon.com/eks/latest/userguide/cni-network-policy-configure.html).
The gateway dynamically fills the placeholder
`infra/k8s/sandbox-job.yaml.template`; do not apply that file directly.

## 6. Configure and test the gateway

Copy `env.example` to a gitignored `.env` and edit it with your own values.
Generate your own secret with `openssl rand -hex 32`; never use the placeholder
or share the secret. Set `EKS_IMAGE` to the digest URI printed in step 3.

```sh
cp env.example .env
chmod 600 .env
openssl rand -hex 32
```

Set these values inside `.env` (plus your generated `SHARED_SECRET`):

```dotenv
LOCAL_MEMORY_BUDGET_MB=256
EKS_ENABLED=true
EKS_CONTEXT=cloudproject-sandbox-lab
EKS_CLUSTER=cloudproject-sandbox-lab
EKS_NODEGROUP=sandbox-workers
EKS_IMAGE=YOUR_ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/cloudproject-sandbox@sha256:YOUR_DIGEST
EKS_MAX_INFLIGHT=3
EKS_NODE_SCALING_ENABLED=true
AWS_REGION=us-east-1
GATEWAY_URL=http://127.0.0.1:8080/submit
```

The optional gateway-run node scaler needs permission for
`eks:DescribeNodegroup` and `eks:UpdateNodegroupConfig`. If your lab denies
those actions, set `EKS_NODE_SCALING_ENABLED=false`; cloud overflow can still
use the single node. This scaler is a lab fallback, not Kubernetes Cluster
Autoscaler. It stops with the local gateway or expired credentials. Do not
attach the proposal in `infra/eks/autoscaler-node-policy.json` to a shared
node role. A dedicated in-cluster autoscaler requires instructor permission
to create/pass an appropriate IAM role.

Start Docker Desktop, then start the local services and open the dashboard:

```sh
.venv/bin/python -m src.dashboard
```

Open `http://127.0.0.1:8000` on the same computer. In a second terminal,
start the project's Nginx proxy from the repository root:

```sh
nginx -t -p "$PWD/nginx/" -c nginx.conf
nginx -p "$PWD/nginx/" -c nginx.conf
```

In a third terminal, check local and cloud paths through Nginx:

```sh
.venv/bin/python -m src.sender.send src/examples/good.py
```

For a quick cloud test, stop the dashboard with Ctrl+C, restart it with
`LOCAL_MEMORY_BUDGET_MB=1 .venv/bin/python -m src.dashboard`, and send the
same example again. The CLI should first show HTTP 202, then a final delivered
result with `execution_location: eks`. Restore the ordinary 256 MB budget by
restarting without that temporary override. The dashboard's System status
page shows live EKS/scaler health and submission metrics.

When finished, stop the project's Nginx proxy with
`nginx -p "$PWD/nginx/" -c nginx.conf -s quit`.

If `kubectl` suddenly cannot reach EKS, refresh lab credentials and check the
API allowlist against your current public IP. Update your own cluster with:

```sh
aws eks update-cluster-config --region "$REGION" --name "$CLUSTER" \
  --resources-vpc-config "publicAccessCidrs=$PUBLIC_IPV4/32"
```

## 7. Clean up your own AWS resources

The EKS control plane and EC2 worker incur charges until deleted, even when
the local dashboard is stopped. Confirm `ACCOUNT_ID` is your account, save any
required evidence, and stop the local dashboard before cleanup. Check that
the namespace has no active Jobs you need. Delete only resources created in
**your account** by these steps:

```sh
aws sts get-caller-identity --query Account --output text
kubectl --context "$CLUSTER" -n cloudproject-sandbox get jobs,pods
aws eks delete-nodegroup --region "$REGION" \
  --cluster-name "$CLUSTER" --nodegroup-name "$NODEGROUP"
aws eks wait nodegroup-deleted --region "$REGION" \
  --cluster-name "$CLUSTER" --nodegroup-name "$NODEGROUP"
aws eks delete-cluster --region "$REGION" --name "$CLUSTER"
aws eks wait cluster-deleted --region "$REGION" --name "$CLUSTER"
aws ec2 delete-launch-template --region "$REGION" \
  --launch-template-name "$LAUNCH_TEMPLATE"
aws ecr delete-repository --region "$REGION" \
  --repository-name "$REPOSITORY" --force
```

Keep the lab's pre-created IAM roles, default VPC, and default subnets.
Do not run cleanup for a shared cluster or another teammate's account.

## References

- [Amazon ECR push workflow](https://docs.aws.amazon.com/AmazonECR/latest/userguide/getting-started-cli.html)
- [AWS CLI create-cluster](https://docs.aws.amazon.com/cli/latest/reference/eks/create-cluster.html)
- [AWS CLI create-nodegroup](https://docs.aws.amazon.com/cli/latest/reference/eks/create-nodegroup.html)
- [VPC CNI network policy setup](https://docs.aws.amazon.com/eks/latest/userguide/cni-network-policy-configure.html)
- [Cluster Autoscaler best practices](https://docs.aws.amazon.com/eks/latest/best-practices/cas.html)
