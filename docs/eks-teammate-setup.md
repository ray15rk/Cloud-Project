# EKS setup for macOS and Linux

This guide connects your local gateway to the **existing**
`cloudproject-sandbox-sydney` EKS cluster in `ap-southeast-2`. It does not
create a new cluster. For PowerShell, use the [Windows guide](eks-teammate-setup-windows.md).
See [AWS access](aws-setup.md) and [deployment details](../infra/eks/README.md).

You do **not** need to create an AWS account. The project owner must
[invite you to the project](aws-setup.md#project-owner-invite-a-teammate)
before you can submit a live dry run. You can install dependencies and run
the automated tests below while waiting for access.

## 1. Install and prepare the project

Install Python 3.11+, AWS CLI v2.32.0 or newer, and a
`kubectl` client compatible with the cluster version shown by
`aws eks describe-cluster`. Run from the repository root:

The `kubectl` client must be within one Kubernetes minor version of the
cluster. Check `kubectl version --client` after checking the cluster version;
for the deployed 1.36 cluster, use a 1.35, 1.36, or 1.37 client.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
test -f .env || cp env.example .env
.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))'
```

To check the code locally without AWS or Docker:

```sh
.venv/bin/python -m pytest src/tests -v -p no:cacheprovider
```

This exercises the mocked EKS path; it does not submit a live dry run.

Put the generated value in `.env` as `SHARED_SECRET`. Preserve an existing
`.env` instead of overwriting it. Keep it out of Git. Docker is not needed
for gateway dry runs.

Create `.env` from `env.example` on each device and generate a unique
`SHARED_SECRET`. Set `KUBECONFIG` to the kubeconfig path on that device.

## 2. Get access to the existing cluster

Before running the commands, accept the owner's
[project invitation](aws-setup.md#project-owner-invite-a-teammate) with your
own AWS Builder ID. You need an authorized project session and network access.
A local profile or kubeconfig cannot grant access by itself.

Sign in with your own project session and profile:

```sh
aws --version
aws login --profile cloudproject-sydney
```

If prompted for a Region, enter `ap-southeast-2`. If the browser displays a legacy "IAM user sign in" page, click **"Change to new experience"** in the top-right banner (or log in directly at [settings.aws.com/projects](https://settings.aws.com/projects) first) to reveal the AWS Builder ID sign-in option. Choose the invited project (`cloudproject-sandbox-sydney`, account `562358727339`)
in the browser. The profile name `cloudproject-sydney` is local to your
device. Use it consistently in these commands:

```sh
aws sts get-caller-identity --profile cloudproject-sydney
aws eks describe-cluster --profile cloudproject-sydney --region ap-southeast-2 \
  --name cloudproject-sandbox-sydney --query 'cluster.{status:status,version:version}'
mkdir -p "$HOME/.kube"
aws eks update-kubeconfig --profile cloudproject-sydney --region ap-southeast-2 \
  --name cloudproject-sandbox-sydney --alias cloudproject-sandbox-sydney \
  --kubeconfig "$HOME/.kube/config-cloudproject-sydney"
test -f "$HOME/.kube/config-cloudproject-sydney"
KUBECONFIG="$HOME/.kube/config-cloudproject-sydney" \
  kubectl --context cloudproject-sandbox-sydney -n cloudproject-sandbox get pods
```

The cluster is in account `562358727339`. Confirm `get-caller-identity` shows
that account and the project's `AccountFullAccessRole`; stop and ask the owner
to check any other identity. Check that the cluster is active and the namespace
query succeeds. If `aws sts` fails, check your invitation, selected project,
and login. If `describe-cluster` is denied, ask the owner to check EKS
permissions. If `kubectl` says Forbidden or Unauthorized, ask the owner to
check the actual role, existing EKS access entry, and Kubernetes permissions.
If `kubectl` times out, ask the operator to compare your current public IP
with the cluster API allowlist; a temporary `/32` entry can be removed after
testing. Never share credentials or commit your IP.

Check the Falco Pod before enabling `FALCO_ENABLED=true`:

```sh
KUBECONFIG="$HOME/.kube/config-cloudproject-sydney" \
  kubectl --context cloudproject-sandbox-sydney -n falco get pods
```

Expect a Ready, Running Falco Pod on each Ready worker node.
The local endpoint needs Bash for Bash payloads (Git for Windows supplies it);
PowerShell payloads currently trigger Falco startup findings. Use Python for the demo.

## 3. Configure the gateway

Edit `.env`. Replace the example profile and kubeconfig placeholders with
your values. Use your actual absolute home path, not the literal `$HOME`
string. Set `EKS_IMAGE` to the digest in [deployment details](../infra/eks/README.md):

Print the exact path to paste after `KUBECONFIG=`:

```sh
printf '%s\n' "$HOME/.kube/config-cloudproject-sydney"
```

```dotenv
EKS_ENABLED=true
EKS_CONTEXT=cloudproject-sandbox-sydney
EKS_IMAGE=PASTE-IMMUTABLE-ECR-IMAGE-AT-SHA256-DIGEST
AWS_REGION=ap-southeast-2
AWS_PROFILE=cloudproject-sydney
KUBECONFIG=/absolute/path/to/.kube/config-cloudproject-sydney
GATEWAY_URL=http://127.0.0.1:8000/submit
FALCO_ENABLED=true
```

Every verified dry run is queued as an EKS Job. With `FALCO_ENABLED=true`,
unavailable monitoring blocks delivery. `EKS_ENABLED=false` disables dry-run
submissions; the gateway never falls back to local Docker.

## 4. Run a smoke test

Start the dashboard from the repository root:

```sh
.venv/bin/python -m src.dashboard
```

Open `http://127.0.0.1:8000`. In another terminal:

```sh
.venv/bin/python -m src.sender.send src/examples/good.py
```

Expect initial HTTP 202 and a final result with `execution_location: eks`.
The final result should contain `delivered: true` and a clean Falco verdict.
The sender polls the returned status URL. Completed polls use HTTP 200 and
carry the final outcome in `status_code`; the CLI prints that final outcome. To test a blocked network attempt:

```sh
.venv/bin/python -m src.sender.send src/examples/network_swallowed.py
```

Expect initial HTTP 202, then final outcome 422 with `delivered: false`,
`execution_location: eks`, and a Falco network finding. Stop the dashboard
after testing. If temporary API access was granted, have the operator remove
that `/32` entry and verify the original allowlist is restored.

For the optional Nginx proxy, set
`GATEWAY_URL=http://127.0.0.1:8080/submit` and start Nginx separately using
the [project README](../readme.md).

Client version guidance: [Kubernetes version skew policy](https://kubernetes.io/releases/version-skew-policy/).
For sign-in profiles, see [AWS project CLI access](https://docs.aws.amazon.com/accounts/latest/reference/connect-ai-coding-tool.html).
