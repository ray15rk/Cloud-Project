# AWS access for the Sydney EKS deployment

The project currently uses a personal AWS account in `ap-southeast-2` (Sydney),
not AWS Academy Learner Lab. The deployed cluster is
`cloudproject-sandbox-sydney`. See [deployment details](../infra/eks/README.md)
for resource names and the verified image digest.

## Sign in and verify the account

Install AWS CLI v2 and configure an authorized profile using your chosen AWS
sign-in method. Keep credentials outside this repository. The operator's local
profile is named `cloudproject-sydney`; teammates should use their own profiles.

Before using EKS, check the identity and region:

```sh
aws sts get-caller-identity --profile cloudproject-sydney
aws eks describe-cluster --profile cloudproject-sydney \
  --region ap-southeast-2 --name cloudproject-sandbox-sydney \
  --query 'cluster.{status:status,version:version}' --output table
```

The deployed AWS account ID is `562358727339`. Stop if the identity command
shows a different account. An AWS identity also needs an EKS access entry or
equivalent Kubernetes authorization to use the cluster. The public API endpoint
is allowlisted by IP, so an authorized user on a new network may need the
operator to update that allowlist.

Do not copy another person's credentials or commit credentials, `.env`,
kubeconfig files, secrets, or the operator's public IP.

## Local gateway and teammate setup

The gateway, dashboard, endpoint, and Nginx proxy run locally. AWS hosts the
sandbox EKS Jobs and their worker nodes. Follow the [macOS/Linux guide](eks-teammate-setup.md)
or [Windows guide](eks-teammate-setup-windows.md) to run a local copy against
the existing Sydney cluster. Local-only mode needs no AWS access.

For a new AWS account, use the [implementation checklist](eks-implementation-plan.md)
as a planning reference. A new cluster needs its own IAM roles, ECR image,
network settings, Cluster Autoscaler association, cost controls, and validation;
copying this repository does not create those resources.

The EKS control plane and worker resources can incur charges even if the local
gateway is stopped. See [shutdown guidance](../infra/eks/README.md).
