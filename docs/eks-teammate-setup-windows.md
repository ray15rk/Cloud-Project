# Set up EKS in your own Learner Lab account on Windows

Run these commands in **PowerShell**, from the repository root. This guide
builds your own private ECR image and creates an EKS cluster in **your own**
AWS account. It does not connect you to Rance's cluster. The gateway and
dashboard run on your Windows computer. For macOS or Linux, use the
[other setup guide](eks-teammate-setup.md).

EKS and EC2 cost money while they exist. Check your lab credit and allowed
services and instance types first. Our lab permitted EKS 1.34 and `t3.medium`;
yours may differ. If a command is denied because of IAM, EKS, EC2, or ECR
restrictions, stop and ask the instructor. Do not use another teammate's
credentials or broaden a shared IAM role to work around a denial.

## 1. Install tools and connect your account

Install Git, AWS CLI v2, `kubectl`, Python 3.11 or newer, Docker Desktop, and
Nginx. Unzip the stable Windows Nginx release to `C:\nginx` (or adjust the
path in step 6). Start Docker Desktop in **Linux containers** mode with the
WSL 2 backend.
Open PowerShell and clone the `rance` branch:

```powershell
git clone -b rance https://github.com/ray15rk/Cloud-Project.git
Set-Location Cloud-Project
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If you already cloned the repository, run `git fetch origin` and
`git switch rance` in it, then `git pull --ff-only origin rance`. Keep this
PowerShell window open because later commands use variables set in it.

Start Learner Lab. Follow [AWS setup](aws-setup.md) to save **your own**
current `[default]` credentials, including the session token, to
`%USERPROFILE%\.aws\credentials`. In PowerShell, you can open that file with:

```powershell
New-Item -ItemType Directory -Force (Join-Path $env:USERPROFILE '.aws') | Out-Null
notepad (Join-Path $env:USERPROFILE '.aws\credentials')
```

Keep credentials outside Git and chat. When the lab session expires, replace
the old `[default]` block with fresh credentials. Then set the names used
throughout this guide and check that the AWS account shown is yours:

```powershell
$Region = 'us-east-1'
$Cluster = 'cloudproject-sandbox-lab'
$NodeGroup = 'sandbox-workers'
$Repository = 'cloudproject-sandbox'
$ImageTag = '20261024-runner1'
$AccountId = aws sts get-caller-identity --query Account --output text
aws sts get-caller-identity --query '{Account:Account,Arn:Arn}' --output json
```

If authentication fails, refresh the lab credentials before proceeding.

## 2. Find your lab roles and default subnets

List the pre-created EKS roles in **your** account. Copy the matching ARNs
from the output; do not copy Rance's ARNs:

```powershell
aws iam list-roles --query "Roles[?contains(RoleName, 'LabEksClusterRole')].[RoleName,Arn]" --output table
aws iam list-roles --query "Roles[?contains(RoleName, 'LabEksNodeRole')].[RoleName,Arn]" --output table
$ClusterRoleArn = 'REPLACE_WITH_YOUR_CLUSTER_ROLE_ARN'
$NodeRoleArn = 'REPLACE_WITH_YOUR_NODE_ROLE_ARN'
$VpcId = aws ec2 describe-vpcs --region $Region --query 'Vpcs[?IsDefault].VpcId | [0]' --output text
aws ec2 describe-subnets --region $Region --filters "Name=vpc-id,Values=$VpcId" 'Name=default-for-az,Values=true' --query 'Subnets[].{ID:SubnetId,AZ:AvailabilityZone,PublicIP:MapPublicIpOnLaunch}' --output table
```

The cluster role must trust EKS. The node role must trust EC2 and allow nodes
to join EKS and pull images from ECR. Ask the instructor if either role is
missing or cannot be passed to EKS. Choose two subnets in **different**
Availability Zones with `PublicIP=True`. Stop if there is no suitable default
VPC/subnet setup; private subnets need different routing.

Find your computer's current public IPv4 using a service you trust, then type
it locally. The `/32` below limits EKS public API access to your current IP.
If your IP later changes, update the allowlist in step 6.

```powershell
$SubnetA = 'REPLACE_WITH_FIRST_SUBNET_ID'
$SubnetB = 'REPLACE_WITH_SECOND_SUBNET_ID'
$PublicIpv4 = 'REPLACE_WITH_YOUR_PUBLIC_IPV4'
```

## 3. Build and push your image to your own ECR

Docker must report `linux`, not `windows`. Build for x86-64 because these
workers are `t3.medium`. Create a private repository, log in, push the image,
and record its immutable digest:

```powershell
docker info --format '{{.OSType}}'
docker build --platform linux/amd64 -t cloudproject-sandbox:dev docker/sandbox
aws ecr create-repository --region $Region --repository-name $Repository
$EcrRegistry = "${AccountId}.dkr.ecr.${Region}.amazonaws.com"
$EcrUri = "$EcrRegistry/$Repository"
aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $EcrRegistry
docker tag cloudproject-sandbox:dev "${EcrUri}:${ImageTag}"
docker push "${EcrUri}:${ImageTag}"
$ImageDigest = aws ecr describe-images --region $Region --repository-name $Repository --image-ids "imageTag=$ImageTag" --query 'imageDetails[0].imageDigest' --output text
$EksImage = "${EcrUri}@${ImageDigest}"
$EksImage
```

The last line should end in `@sha256:...`. If the repository already exists,
skip `create-repository`. Never use another account's private ECR URI.

## 4. Create the cluster and a one-to-two-node worker group

The tested cluster used Kubernetes 1.34. If your lab only allows another
version, choose a supported version and compatible add-ons. Later use of
Kubernetes Cluster Autoscaler requires a version matching the cluster minor
version. These commands create the cluster and configure `kubectl` for it:

```powershell
aws eks create-cluster --region $Region --name $Cluster --version 1.34 --role-arn $ClusterRoleArn --resources-vpc-config "subnetIds=$SubnetA,$SubnetB,endpointPublicAccess=true,endpointPrivateAccess=true,publicAccessCidrs=$PublicIpv4/32"
aws eks wait cluster-active --region $Region --name $Cluster
aws eks update-kubeconfig --region $Region --name $Cluster --alias $Cluster
kubectl --context $Cluster get namespaces
```

The checked-in launch template data requires IMDSv2. Run from the repo root
so AWS CLI can read the `file://` path. EKS picks the compatible node AMI;
the node group uses `t3.medium` and starts at one node:

```powershell
$LaunchTemplate = 'cloudproject-eks-nodes'
aws ec2 create-launch-template --region $Region --launch-template-name $LaunchTemplate --launch-template-data file://infra/eks/launch-template-data.json
aws eks create-nodegroup --region $Region --cluster-name $Cluster --nodegroup-name $NodeGroup --node-role $NodeRoleArn --subnets $SubnetA $SubnetB --instance-types t3.medium --ami-type AL2023_x86_64_STANDARD --capacity-type ON_DEMAND --scaling-config minSize=1,maxSize=2,desiredSize=1 --labels workload=cloudproject-sandbox --launch-template "name=$LaunchTemplate,version=1"
aws eks wait nodegroup-active --region $Region --cluster-name $Cluster --nodegroup-name $NodeGroup
kubectl --context $Cluster get nodes -L workload
```

Wait for one `Ready` node labeled `workload=cloudproject-sandbox`. The maximum
of two only sets a limit; it does not enable automatic scaling by itself. If
the group fails, inspect `aws eks describe-nodegroup` health issues, role
policies, subnet public IP settings, and the lab's EC2 restrictions.

## 5. Enable sandbox network isolation

Kubernetes NetworkPolicy needs an enforcing network policy engine. We use the
managed Amazon VPC CNI add-on. Check whether `vpc-cni` is listed:

```powershell
aws eks list-addons --region $Region --cluster-name $Cluster --output table
```

If it is **absent**, run this `create-addon` command:

```powershell
aws eks create-addon --region $Region --cluster-name $Cluster --addon-name vpc-cni --resolve-conflicts OVERWRITE --configuration-values file://infra/eks/vpc-cni-policy.json
```

If it is **present**, run this `update-addon` command instead:

```powershell
aws eks update-addon --region $Region --cluster-name $Cluster --addon-name vpc-cni --configuration-values file://infra/eks/vpc-cni-policy.json
```

Wait for the managed add-on, apply the CoreDNS allowance **before** switching
to strict mode, then apply the restricted sandbox namespace:

```powershell
aws eks wait addon-active --region $Region --cluster-name $Cluster --addon-name vpc-cni
kubectl --context $Cluster apply -f infra/k8s/coredns-networkpolicy.yaml
kubectl --context $Cluster apply -f infra/k8s/sandbox-namespace.yaml
aws eks update-addon --region $Region --cluster-name $Cluster --addon-name vpc-cni --configuration-values file://infra/eks/vpc-cni-policy-strict.json
aws eks wait addon-active --region $Region --cluster-name $Cluster --addon-name vpc-cni
kubectl --context $Cluster get networkpolicy -A
kubectl --context $Cluster -n cloudproject-sandbox get resourcequota
kubectl --context $Cluster -n kube-system get ds aws-node -o yaml
```

In the `aws-node` DaemonSet output, check for the
`aws-network-policy-agent` container and an environment variable named
`NETWORK_POLICY_ENFORCING_MODE` with value `strict`. Stop before running
payloads if either is missing. If the add-on fails, inspect
`aws eks describe-addon` health issues. The gateway fills in
`infra/k8s/sandbox-job.yaml.template` at runtime; do not apply it directly.

## 6. Configure and test the local gateway

Copy the example settings to the gitignored `.env` file if it does not exist.
Generate a unique
secret and paste it as `SHARED_SECRET` in Notepad. Also paste `$EksImage` as
`EKS_IMAGE` and set these values:

```powershell
if (-not (Test-Path .env)) { Copy-Item env.example .env }
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
notepad .env
```

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

Do not commit or share `.env`. The optional gateway-run node scaler needs
`eks:DescribeNodegroup` and `eks:UpdateNodegroupConfig`. If your lab denies
these, set `EKS_NODE_SCALING_ENABLED=false`; cloud overflow can still use the
single worker. This scaler runs only while the local gateway and temporary
AWS credentials are active. Do not attach
`infra/eks/autoscaler-node-policy.json` to a shared node role. An in-cluster
Kubernetes Cluster Autoscaler needs an instructor-approved IAM setup.

With Docker Desktop running, start the dashboard in one PowerShell window:

```powershell
.\.venv\Scripts\python.exe -m src.dashboard
```

Open `http://127.0.0.1:8000` locally for the dashboard. Start the project's
Nginx proxy in a **second PowerShell window** from the repository root:

```powershell
$NginxPrefix = (Join-Path (Get-Location).Path 'nginx').Replace('\', '/') + '/'
& 'C:\nginx\nginx.exe' -t -p $NginxPrefix -c nginx.conf
& 'C:\nginx\nginx.exe' -p $NginxPrefix -c nginx.conf
```

The sender uses Nginx at port 8080, which also proxies the result polling
requests. In a **third PowerShell window** in the repository, send a test:

```powershell
.\.venv\Scripts\python.exe -m src.sender.send src/examples/good.py
```

To force a cloud overflow test, stop the dashboard with Ctrl+C in its window,
then restart it with a tiny local memory budget:

```powershell
$env:LOCAL_MEMORY_BUDGET_MB = '1'
.\.venv\Scripts\python.exe -m src.dashboard
```

Send the same example again from the third window. Expect HTTP 202 followed
by a final result with `execution_location: eks`. Stop the dashboard and
restore the normal `.env` setting before starting it again:

```powershell
Remove-Item Env:\LOCAL_MEMORY_BUDGET_MB
.\.venv\Scripts\python.exe -m src.dashboard
```

If `kubectl` cannot reach EKS later, refresh your lab credentials and check
whether your public IP changed. Update **your** cluster's API allowlist with:

```powershell
$PublicIpv4 = 'YOUR_CURRENT_PUBLIC_IPV4'
aws eks update-cluster-config --region $Region --name $Cluster --resources-vpc-config "publicAccessCidrs=$PublicIpv4/32"
```

## 7. Clean up your own AWS resources

The EKS control plane and EC2 node cost money until deleted, even if the
dashboard is stopped. Save any evidence you need first. Confirm you are still
in **your** account and that no Jobs need to finish. Stop the local dashboard,
then remove only the resources created by this guide:

```powershell
aws sts get-caller-identity --query Account --output text
kubectl --context $Cluster -n cloudproject-sandbox get jobs,pods
aws eks delete-nodegroup --region $Region --cluster-name $Cluster --nodegroup-name $NodeGroup
aws eks wait nodegroup-deleted --region $Region --cluster-name $Cluster --nodegroup-name $NodeGroup
aws eks delete-cluster --region $Region --name $Cluster
aws eks wait cluster-deleted --region $Region --name $Cluster
aws ec2 delete-launch-template --region $Region --launch-template-name $LaunchTemplate
aws ecr delete-repository --region $Region --repository-name $Repository --force
```

Keep the lab's pre-created IAM roles, default VPC, and subnets. If you opened
a new PowerShell window, set the variables in steps 1 and 4 again before
cleanup. Shut down the project Nginx process with
`& 'C:\nginx\nginx.exe' -p $NginxPrefix -c nginx.conf -s quit` in its window.
Do not delete a shared cluster or another teammate's resources.

## References

- [Docker Desktop on Windows](https://docs.docker.com/desktop/setup/install/windows-install/)
- [Amazon ECR push workflow](https://docs.aws.amazon.com/AmazonECR/latest/userguide/getting-started-cli.html)
- [AWS CLI file parameters](https://docs.aws.amazon.com/cli/latest/userguide/cli-usage-parameters-file.html)
- [VPC CNI network policy setup](https://docs.aws.amazon.com/eks/latest/userguide/cni-network-policy-configure.html)
