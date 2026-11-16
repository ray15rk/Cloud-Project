# AWS access for the Sydney EKS cluster

Relay's live sandbox is the existing `cloudproject-sandbox-sydney` EKS cluster
in project account `562358727339`, Region `ap-southeast-2`. This account uses
AWS's new project experience. A teammate does **not** need a separate AWS
account or IAM user. The project owner shares the
project through AWS Settings; each teammate accepts with their own AWS Builder
ID and creates their own local AWS CLI profile and kubeconfig.

## Choose the test you need

| Goal | Project invitation needed? | Start here |
| --- | --- | --- |
| Install dependencies and run automated tests without Docker | No | [Local test instructions](../readme.md#51-pytest-test-suite) |
| Submit a real dry run or demo the dashboard against EKS | Yes | Follow the invitation and local setup below, then the [macOS/Linux](eks-teammate-setup.md) or [Windows](eks-teammate-setup-windows.md) guide |

The automated tests use fake Kubernetes responses and local test servers;
they do not prove that the live EKS cluster is reachable. Every gateway dry
run uses EKS. There is no Docker fallback.

## Project owner: invite a teammate

**Review the access scope first:** AWS grants an invited team member admin
access to the shared project's AWS resources. The owner cannot limit the
invitation to EKS or make it read-only. A team member cannot change the
project's billing settings or invite other members. If that scope is too
broad, the project owner can run the live demo while teammates run the local
tests without AWS.

1. Sign in at [AWS Settings](https://settings.aws.com).
2. Open **Team** and choose **Invite new team member**.
3. Enter the teammate's own email address. Select **only the project whose
   account ID is `562358727339`**. Check the project account ID on its detail
   page if several projects appear.
4. Choose **Send invitation**. The teammate has 14 days to accept. They use
   or create their own AWS Builder ID for the invited email address.

The project owner should send the teammate the cluster name, Region, these
setup guides, and the [sandbox image digest](../infra/eks/README.md#sandbox-image).
Each person creates their own CLI profile and `.env` on their device.

This cluster already has an EKS access entry for AWS's project
`AccountFullAccessRole`, the role used in the owner's current CLI session.
**The first invited teammate still needs to verify their own CLI identity and
Kubernetes access:** project invitation alone is not proof that the EKS API
and namespace RBAC work from their device. If their role differs or `kubectl`
returns Forbidden, the owner must inspect the EKS access entry and the
[sandbox](../infra/k8s/gateway-rbac.yaml) and
[Falco](../infra/falco/gateway-falco-rbac.yaml) RoleBindings before proceeding.

The EKS public API also has an IP allowlist. If an authorized teammate's
`kubectl` request times out, the owner can temporarily add that teammate's
current public `/32`, then remove it after testing. Do not commit IPs to the
repository.

## Teammate: sign in on your own device

After accepting the project invitation, install AWS CLI **2.32.0 or newer**.
Use the browser-based project session to create your own named profile:

```sh
aws --version
aws login --profile cloudproject-sydney
aws sts get-caller-identity --profile cloudproject-sydney
```

If prompted for a Region, enter `ap-southeast-2`. In the browser, choose the
**invited project**, not another AWS session. Confirm that the identity's
account is `562358727339`; the role should be the project's
`AccountFullAccessRole` for the existing access entry. Do not continue with
another account or role until the owner checks it. Renew an expired session
with the same `aws login --profile cloudproject-sydney` command.

Then follow the platform guide to run `aws eks update-kubeconfig` and put
your **own absolute kubeconfig path** in your `.env`.

| Symptom | Check |
| --- | --- |
| No project in browser login | Invitation accepted with the same email/Builder ID; correct project selected |
| `get-caller-identity` shows another account | Wrong browser session or profile |
| `describe-cluster` is denied | Project session and EKS permissions |
| `kubectl` is Unauthorized or Forbidden | Actual IAM role, EKS access entry, and namespace RoleBindings |
| `kubectl` times out | Current public IP against the EKS API allowlist |

The local gateway, dashboard, endpoint, and optional NGINX proxy run on your
device; AWS runs sandbox Jobs. EKS resources can incur charges even when your
local gateway is stopped. See [shutdown guidance](../infra/eks/README.md).

AWS references: [project invitations](https://docs.aws.amazon.com/accounts/latest/reference/invite-team-members.html),
[project CLI access](https://docs.aws.amazon.com/accounts/latest/reference/connect-ai-coding-tool.html),
and [CLI browser login](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html).
