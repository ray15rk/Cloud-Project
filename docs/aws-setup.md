# AWS Learner Lab setup

## 1. Connect the lab

Start AWS Academy Learner Lab. Open **AWS Details > AWS CLI > Show** and copy the
complete credentials block, including the session token. In a Windows terminal:

```cmd
if not exist "%USERPROFILE%\.aws" mkdir "%USERPROFILE%\.aws"
notepad "%USERPROFILE%\.aws\credentials"
```

On macOS or Linux, create `~/.aws/credentials` with a local editor instead:

```sh
mkdir -p ~/.aws
chmod 700 ~/.aws
nano ~/.aws/credentials
chmod 600 ~/.aws/credentials
```

Save the copied `[default]` block in that file. If the file already contains a
`[default]` block, replace that block with the current lab values and preserve
unrelated profiles. Keep credentials outside this repository. Refresh them from
the lab when they expire.

## 2. Check AWS access

From the project directory, run this in either Command Prompt or PowerShell:

```cmd
powershell -NoProfile -ExecutionPolicy RemoteSigned -File scripts\check-aws.ps1
```

The script defaults to `us-east-1`. If your lab specifies another region, append
`-Region REGION`. It checks your identity and reads ECR repositories and EKS
clusters. Empty lists are expected for a new account. No resources are created.
These read checks do not establish permission to create infrastructure.

If EKS is denied, check the lab Readme and ask your instructor for EKS access
before provisioning. The proposal requires EKS; another service is not an
equivalent implementation of that requirement.

On macOS or Linux, run these equivalent read-only checks after setting the lab's
region (replace `REGION` with the value in your lab instructions):

```sh
aws sts get-caller-identity --region REGION
aws ecr describe-repositories --region REGION --max-items 1
aws eks list-clusters --region REGION --max-items 1
```

Do not paste credential values into chat, terminal output, or this repository.

## 3. Recreate the EKS deployment

For exact commands to build/push the image, create a cluster and bounded node
group in your own Learner Lab account, enable sandbox network isolation, run an
overflow test, and clean up, follow the [macOS/Linux guide](eks-teammate-setup.md)
or [Windows PowerShell guide](eks-teammate-setup-windows.md).
The outline below explains the sequence those commands implement.

1. Create an ECR repository and push `cloudproject-sandbox:dev`.
2. Verify the lab's available IAM roles and EKS provisioning permissions. Set up
   the Auto Scaling service-linked role during deployment if authorized and needed.
3. Provision EKS with bounded worker capacity and node autoscaling. Enable network
   policy enforcement before running sandbox payloads.
4. Run the image as a disposable Kubernetes Job with resource limits, deadlines,
   a restrictive security context, no service-account token or Docker socket,
   isolated networking, disabled payload retries, and automatic cleanup.
5. Configure the existing gateway's EKS execution path and bounded retries.
6. Demonstrate two concurrent local runs plus one cloud run, then capture failure,
   timing, overload, notification, and cleanup results for the project report.

Keep manifests and provisioning configuration in the repository so the setup can
be reproduced. Follow the lab's cleanup instructions between sessions; ending a
session does not necessarily stop every billable service.
