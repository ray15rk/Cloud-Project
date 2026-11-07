# Falco behavioural threat detection

This adds runtime behaviour monitoring to the EKS dry run. Before, a dry run was judged by its
**outcome**: timeout, OOM kill, or a non-zero exit code. That misses a script that tries something
forbidden, catches the error and exits 0. The sandbox's own isolation hides the behaviour.

Falco runs on each EKS node, **outside** the sandbox, and reports what the script actually tried at
the syscall level: opening a network socket, writing a file, starting another program, reading
credentials, calling privileged syscalls. The gateway refuses to forward any payload whose dry run
showed such behaviour, even when it exited 0.

## How it works

```
 signed payload ──> gateway: verify HMAC ──> EKS Job (sandbox Pod) ──> Job ends
                                                   │ syscalls
                                                   ▼
                                       Falco on the same node (eBPF)
                                       rules: infra/falco/cloudproject_rules.yaml
                                                   │ JSON alerts to the Falco pod's log
                                                   ▼
 gateway reads that log through the Kubernetes API, keeps the alerts for THIS Pod
   observed alert present + no threat alerts  ->  clean      -> forward to the endpoint
   any threat alert                           ->  threat_detected (HTTP 422), never forwarded
   no observed alert, or Falco unreachable    ->  infrastructure_error (HTTP 503), never forwarded
```

The gateway runs on your laptop and the cluster cannot call it, so alerts are **pulled** through the
Kubernetes API (read-only access to the Falco pod's log) rather than pushed.

**Fail closed.** One rule, `CP Sandbox Observed`, fires when the sandbox runner starts as the
container's PID 1. If the gateway never sees it, Falco was not watching that run, and the run is not
called clean. This also catches a broken image: if the ENTRYPOINT is lost, `runner.py` never starts,
the alert never appears, and the run is rejected.

## What counts as a threat

| Rule | Behaviour class | Fires when a payload... |
|---|---|---|
| CP Sandbox Network Socket | `network` | creates an IP or raw packet socket (even if `connect()` would hang) |
| CP Sandbox Network Connect | `network` | calls `connect()` on an IP socket (adds the destination) |
| CP Sandbox File Write Attempt | `filesystem` | opens a file for writing, including blocked attempts (read-only root filesystem) |
| CP Sandbox Sensitive Path Access | `credentials` | opens `/var/run/secrets`, `/etc/shadow`, `~/.aws`, `~/.kube`, ... |
| CP Sandbox Unexpected Program | `process` | starts any program other than `python`, `python3`, `bash`, `pwsh` |
| CP Sandbox Privileged Syscall | `privilege` | calls `ptrace`, `setns`, `unshare`, `bpf`, `init_module`, `finit_module` |

Policy lives in the `cp_*` lists at the top of `infra/falco/cloudproject_rules.yaml`. To change what
is allowed, edit a list, not a rule. CPU and memory abuse are **not** Falco's job: the Job's limits and
the 10-second deadline in `docker/sandbox/runner.py` already handle those.

## What was added or changed

| File | Change |
|---|---|
| `infra/falco/cloudproject_rules.yaml` | **New.** The rules and the policy lists |
| `infra/falco/values.yaml` | **New.** Helm values for the Falco chart |
| `infra/falco/falco-namespace.yaml` | **New.** `falco` namespace (privileged) and its egress network policy |
| `infra/falco/gateway-falco-rbac.yaml` | **New.** Read-only access for the gateway to Falco's log |
| `src/sandbox/falco_monitor.py` | **New.** Reads Falco's alerts and produces the verdict |
| `src/sandbox/eks_runner.py` | Applies the verdict; new status `threat_detected`; `monitor_healthy()` |
| `src/gateway/cloud_worker.py` | Includes `behaviour` findings in API responses and the activity log |
| `src/gateway/gateway_app.py` | `EXECUTION_MODE`, `EKS_WORKERS`, startup warnings |
| `src/gateway/dashboard.py`, `static/dashboard.js` | "Behaviour monitor" health row |
| `infra/k8s/sandbox-job.yaml.template`, `docker/sandbox/Dockerfile` | Explicit Job `command`/`args` and restored `ENTRYPOINT` (see below) |
| `scripts/check_falco.py` | **New.** Live canary test against your cluster |
| `src/examples/*.py` | **New.** Five canary payloads that each exit 0 |
| `src/tests/falco_testkit.py`, `test_falco_monitor.py`, `test_falco_runner.py`, `test_falco_rules.py`, `test_sandbox_job_template.py` | **New.** Unit tests and their shared fakes |

**Why the Job command changed.** The Dockerfile lost its `ENTRYPOINT` in the polyglot merge. An image
built from it runs the base image's default `python`, which exits 0 having run nothing, so a Job would
report Complete for *any* script. The Job now names its command explicitly, and the Dockerfile has its
`ENTRYPOINT` back. Falco's "observed" rule depends on `runner.py` being PID 1.

## Prerequisites

- The EKS cluster from `infra/eks/README.md`, with `kubectl` and `helm` installed and your context set
  (examples use `cloudproject-sandbox-sydney`).
- Nodes on a kernel with BTF, which the eBPF probe needs. Check in step 1; Amazon Linux 2023 normally has it.
- Node access to Docker Hub (Falco images) and `ghcr.io` (the container plugin, fetched once at pod start).
  If your nodes only reach ECR, mirror the images first.
- Room on the nodes. The `t3.small` is 2 GiB. Falco requests 50m CPU / 200Mi and is limited to 500m /
  512Mi (see `values.yaml`). Each sandbox requests 500m CPU, so a node that held three sandboxes may
  now hold two. Check with `kubectl describe node`.

## Setup

Commands work in PowerShell and bash unless noted. Run from the repo root.

**1. Check the nodes support the eBPF probe**

```
kubectl --context cloudproject-sandbox-sydney get nodes -o wide
kubectl --context cloudproject-sandbox-sydney debug node/<node-name> -it --image=busybox -- ls /host/sys/kernel/btf/vmlinux
```

The second command should print the path. If the file is missing, this probe will not work on that
node (use `driver.kind: kmod` in `values.yaml` instead, which needs kernel headers).

**2. Rebuild and push the sandbox image** (recommended, because the Dockerfile changed). Use the build
and push commands in `docs/eks-teammate-setup.md` (macOS/Linux) or `docs/eks-teammate-setup-windows.md`
(Windows), then put the new digest in `EKS_IMAGE` in `.env`. The Job now works with the old image too,
because it passes its own command.

**3. Create the namespace and network policy**

```
kubectl --context cloudproject-sandbox-sydney apply -f infra/falco/falco-namespace.yaml
```

**4. Install Falco.** The chart version is pinned because `values.yaml` was checked against 9.2.x. If
Helm cannot find it, run `helm search repo falcosecurity/falco --versions` and take the nearest 9.2.x.

```
helm repo add falcosecurity https://falcosecurity.github.io/charts
helm repo update
helm upgrade --install falco falcosecurity/falco --version 9.2.2 --namespace falco --kube-context cloudproject-sandbox-sydney -f infra/falco/values.yaml --set-file "customRules.cloudproject_rules\.yaml=infra/falco/cloudproject_rules.yaml"
```

**5. Check Falco is healthy.** Expect one `falco-...` pod per node, `Running`:

```
kubectl --context cloudproject-sandbox-sydney -n falco get pods -o wide
kubectl --context cloudproject-sandbox-sydney -n falco logs daemonset/falco -c falco --tail=60
kubectl --context cloudproject-sandbox-sydney -n falco exec daemonset/falco -c falco -- ls /etc/falco/rules.d
```

The last command must list `cloudproject_rules.yaml`. A rule that Falco cannot parse shows up as an
error in the log and the pod restarts; see Troubleshooting.

**6. Give the gateway read access to Falco's log**

```
kubectl --context cloudproject-sandbox-sydney apply -f infra/falco/gateway-falco-rbac.yaml
```

The binding is for the group `cloudproject-gateway`, like `infra/k8s/gateway-rbac.yaml`. If your kubeconfig
identity is the cluster creator it may already have this access, but apply it anyway so the gateway also
works with a least-privilege identity.

**7. Turn it on in `.env`** (see `env.example` for all keys)

```dotenv
EKS_ENABLED=true
FALCO_ENABLED=true
EXECUTION_MODE=cloud
```

`EXECUTION_MODE=cloud` makes every dry run an EKS Job so every run is watched. Without it (the default,
`hybrid`) local Docker runs are **not** monitored, and the gateway prints a warning saying so.

**8. Run the live canary test**

```
python -m scripts.check_falco
```

It runs eight small scripts as real EKS Jobs, one at a time. Each waits for a Job to start and run, then a few
seconds for alerts, so allow tens of seconds per canary; the first can be slower while an image pulls or a
node starts. EKS timings have not been measured yet, so treat that as a rough guide. Every canary exits 0.

| Canary | Expected status | Expected behaviour |
|---|---|---|
| `benign` | `OK` | none |
| `network_swallowed` | `threat_detected` | `network` |
| `exec_child` | `threat_detected` | `process` |
| `fs_write_swallowed` | `threat_detected` | `filesystem` |
| `credential_probe` | `threat_detected` | `credentials` |
| `privileged_syscall` | `threat_detected` | `privilege` (**informational**: a miss is a WARN) |
| `benign_bash` | `OK` | none (**informational**) |
| `benign_powershell` | `OK` | none (**informational**; currently WARN on this cluster) |

Output looks like `[PASS] network_swallowed  status=threat_detected  behaviours=network ...`. The script
exits non-zero if a required canary does not match. Use `--only network_swallowed` to run one, `--json`
for machine-readable output.

**9. Try it through the full pipeline.** Start the stack as in `readme.md` section 4, then:

```
python -m src.sender.send src/examples/good.py
python -m src.sender.send src/examples/network_swallowed.py
```

The first is delivered. The second returns `202`, then a final `422` with `reason` starting
`threat_detected:` and a `behaviour.findings` list. The endpoint never receives it. The dashboard shows
a **Behaviour monitor** row in its health panel.

## Running the unit tests

```
python -m pytest src/tests -m "not docker" -q
```

These use fake Kubernetes and realistic Falco JSON, so they need no cluster. They check the parsing, the
fail-closed paths, the contract between the rules and the gateway, and that the Helm values keep the
settings detection depends on.

## Tuning

- **A benign script is flagged.** Read the finding's `evidence` in the API response. If the behaviour is
  legitimate, add it to the matching list in `infra/falco/cloudproject_rules.yaml` (for example a program
  to `cp_allowed_programs`), then redeploy the rules: re-run the `helm upgrade` command in step 4.
- **Add a language.** When Bash or PowerShell run on EKS, the interpreters are already allowed. PowerShell
  may write under its home directory and start helper processes, which will show up as findings until you
  allow them or give it a writable scratch volume.
- **Wait times.** `FALCO_SETTLE_SECONDS` (default 3) is how long the gateway waits after the Job ends and
  again after the first observed marker for late alerts. `FALCO_WAIT_SECONDS` (default 120) is how long it
  waits for the "observed" alert.
  The log query includes at least 120 seconds because a shorter EKS log window excluded fresh alerts.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `no running Falco pod on node ...` | The Falco pod is not running on that node, often a freshly autoscaled one. Check `kubectl -n falco get pods -o wide`, wait, resubmit. |
| `could not read Falco logs (403)` or `could not list Falco pods` | RBAC missing. Apply `infra/falco/gateway-falco-rbac.yaml`. |
| `Falco did not report the sandbox starting (...)` | The text in brackets says why. *Log window was empty*: wrong Falco pod or container, or nothing written yet. *None of the log lines parsed as JSON*: check `falco.json_output` is true (`infra/falco/values.yaml`). *No alerts from the CloudProject rules*: the rules are not loaded (step 5) or the container/`k8s.*` fields are missing; grep `kubectl -n falco logs daemonset/falco -c falco \| grep "CP Sandbox"`. *Other Pods only*: the alert's `container.id` / `k8s.pod.name` does not match this Pod, or the Falco pod read is on another node. *Matched but no start alert*: the `observed` rule is missing, or `runner.py` did not start as the container's PID 1 (image ENTRYPOINT, or the Job command). |
| Falco pod `CrashLoopBackOff` | `kubectl -n falco logs <pod> -c falco --previous`. Usual causes: unsupported kernel or no BTF, a rule error after editing the rules, or `OOMKilled` (raise `resources.limits.memory`). |
| `falcoctl-artifact-install` init container fails | It downloads the container plugin from `ghcr.io` over TCP 443. Check NAT and the `falco-egress` policy. |
| Every run is `threat_detected` | A finding that fires on every run is usually the runtime's own setup. Read the `evidence`. `cp_sandbox_workload` already excludes `runc`; another helper process may need excluding. |
| `privileged_syscall` canary is `WARN` | Expected on some nodes: the Pod's seccomp profile can deny these syscalls before Falco's probe sees them. They are still blocked. |

## Limits to state honestly

- **EKS only.** Local Docker runs are not monitored. Use `EXECUTION_MODE=cloud` for full coverage.
- **After the fact.** Alerts are read once the Job ends (plus the settle time). A run is never killed
  mid-flight by Falco; it is rejected before forwarding. An alert arriving later than the settle plus
  wait window would be missed.
- **One execution path.** Dynamic analysis sees only what the script does in that run. A script that
  waits, or detects it is in a sandbox, can behave differently on the real device.
- **Seccomp ordering.** Syscalls the Pod's seccomp profile denies outright may never reach the probe.
- **Dropped events.** Under extreme load Falco can drop syscall events. The "observed" check proves
  Falco saw the start, not that it saw everything.
- **Alert visibility.** Anyone who can read the Falco log can see sandbox command lines in the evidence.
- **Live verification (2026-11-01).** On `cloudproject-sandbox-sydney`, the clean Python and Bash
  canaries passed; network, process, filesystem, credential, and privileged syscall canaries were
  detected. The real sender, NGINX, gateway, EKS/Falco, and local edge endpoint delivered clean Python
  and Bash submissions (HTTP 200). A network-attempt submission was rejected (HTTP 422); the endpoint
  log showed no request for it. Some clean runs also failed closed with HTTP 503 because the Falco
  start marker appeared too late for the gateway's earlier log polling window. Another submission
  briefly failed before a Job result was available. The longer wait reduces timing failures, but
  sustained multi-run reliability has not yet been demonstrated.
- **PowerShell is not ready for clean delivery.** Its Job now starts with a small in-memory `/tmp`,
  but the PowerShell runtime itself opens network sockets and writes startup files. Falco correctly
  blocks the current benign PowerShell canary. Do not exempt all `pwsh` network activity; that would
  also hide a payload's network attempts. Keep PowerShell submissions blocked until its runtime noise
  can be separated safely from payload behaviour and verified with live canaries.
- **Language handoff.** The gateway stores the signed language with each queued submission and passes
  it to the EKS Job and edge endpoint. Python and Bash have passed live canaries.
- **Resources.** Falco runs on every node and uses memory the sandboxes could have used.

## Remove or roll back

```
helm uninstall falco --namespace falco --kube-context cloudproject-sandbox-sydney
```

Then set `FALCO_ENABLED=false` in `.env` and restart the gateway. Behaviour returns to outcome-only
checks. The explicit Job command and restored ENTRYPOINT stay; they are safe on their own.
