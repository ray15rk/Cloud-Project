# Project review 12 November 2026

The gateway queues every admitted dry run on EKS. Local Docker is retained only
as a legacy helper and test target. The README, teammate setup, AWS access,
Falco deployment notes, historical evaluation notes and Group15 paper were
checked against the gateway, worker, endpoint and Kubernetes manifests.

## Verification

| Check | Result |
| --- | --- |
| Complete pytest suite | 160 passed in 8.91 seconds, including 9 legacy Docker tests |
| Non-Docker suite | 151 passed; includes loopback Kubernetes client integration tests |
| Installed Python dependency compatibility | `pip check`: no broken requirements |
| Dashboard JavaScript | Node syntax check passed |
| Chrome UI smoke test | Page load, sample selection, tamper rejection 401, mobile viewport and no JavaScript errors; EKS disabled for this check |
| Local Markdown links | All checked targets exist |
| Group15 paper | Six pages rendered and visually checked |
| NGINX configuration | `nginx -t` passed |
| Live AWS/EKS rerun | Required canaries passed; seven NGINX pipeline scenarios passed; known PowerShell warning reproduced |

The first restricted run could not bind loopback sockets and Docker was stopped.
After granting loopback access and starting Docker Desktop, the full suite passed.
Unit tests mock EKS and do not prove the current cluster is healthy. Windows
instructions were reviewed, but this run used macOS; Windows was not executed.

## Corrections

- Removed active Docker flag, local-only mode and four-local-slot claims from
  current setup instructions. Historical results remain explicitly historical.
- Clarified direct dashboard port 8000 versus NGINX port 8080, local language
  dependencies, preserving `.env`, and profile-specific session refresh.
- Removed the Windows command that killed all Python processes.
- Documented that accepted submissions return 202, while completed polls return
  HTTP 200 with a final outcome in the JSON `status_code` field.
- Corrected the PowerShell demo expectation: Falco currently blocks runtime
  startup behavior. Updated dashboard sample descriptions accordingly.
- Corrected paper claims about authenticated endpoint delivery, a stateless
  gateway, metadata-only logging, NGINX routes and universal safety/novelty.
- Updated the paper's automated test count and explained the different scope of
  current tests and historical live benchmarks.
- Added `demo-runbook.md` with preflight, recording steps and cleanup.

## Remaining gaps before submission or wider deployment

1. The filename says Group15 but the author block says Team 22. Confirm the
   required group label. Three author GUID fields remain blank; use real values.
2. The paper's historical benchmark numbers were preserved as recorded results.
   Raw benchmark reports are not present in this checkout, so those numerical
   results could not be independently recalculated during this review. Attach
   the dated measurements or rerun them before treating them as reproducible.
3. The bibliography contains bare URLs and does not map all technical claims or
   comparison rows to sources. Complete the required academic citation format
   and a claim-to-source check before final submission. The comparison is now
   framed as architectural roles, not an exhaustive novelty claim.
4. `scripts/check_dashboard.py`, `scripts/check_stack.py` and
   `scripts/check_nginx.py` retain assumptions about the old synchronous Docker
   flow. Do not use them as current EKS acceptance tests. The pytest suite is
   current; a browser-level asynchronous EKS acceptance harness remains work.
5. The endpoint executes natively and has no caller authentication. Keep it on
   loopback. A clean sandbox run is not a safety proof for another environment.
6. Sustained EKS/Falco reliability, clean PowerShell delivery, fresh-account
   provisioning, verified least-privilege IAM/RBAC and cost ownership remain
   deployment work, as described in the deployment notes.
7. EKS does not declare the legacy Docker helper's 64-process limit. The Falco
   observed marker proves observation started, not complete event capture.

## Scope

This was a functional and documentation review with regression testing, not a
formal security audit or an exhaustive proof of correctness. The live checks below are separate from the local automated suite. No new
autoscaling or sustained-load benchmark was performed.

## Source checks

- [Kubernetes version skew policy](https://kubernetes.io/releases/version-skew-policy/)
  supports the guides' one-minor-version kubectl compatibility rule.
- [AWS project invitations](https://docs.aws.amazon.com/accounts/latest/reference/invite-team-members.html)
  document teammate access through AWS Settings; [project CLI access](https://docs.aws.amazon.com/accounts/latest/reference/connect-ai-coding-tool.html)
  documents each teammate's `aws login` profile.
- The paper's first reference resolves to Bauwens et al., “Over-the-Air Software
  Updates in the Internet of Things: An Overview of Key Principles,” IEEE
  Communications Magazine 58(2), 35–41, February 2020,
  DOI [10.1109/MCOM.001.1900125](https://doi.org/10.1109/MCOM.001.1900125).
- The [Mender reference](https://mender.io/resources/reports-and-guides/ota-updates-best-practices)
  resolves to its May 2025 OTA best-practices guide. It does not support the
  paper's earlier blanket claim that all Mender updates are monolithic images;
  that claim was removed.
- IEEE document `9082645` could not be verified: its page requires a browser
  challenge and a title could not be confirmed. Confirm its title and relevance
  before final submission.


## Live retest after AWS sign-in

The existing EKS worker and Falco Pod were Ready. This machine's IP was already
allowed, so no API allowlist change was made. The allowlist was unchanged after
testing. No labeled sandbox Jobs, Pods or ConfigMaps remained; temporary local
HTTP services and the test NGINX instance stopped.

All five required canaries passed: clean Python, network attempt, child process,
file write and credential access. The informational privileged-syscall and clean
Bash canaries also passed. PowerShell again produced network/filesystem findings;
its informational clean-delivery expectation remains a warning.

| Real NGINX pipeline scenario | Result |
| --- | --- |
| Clean Python | Final outcome 200, EKS execution, clean Falco verdict, delivered |
| Clean Bash | Final outcome 200, EKS execution, clean Falco verdict, delivered |
| Swallowed network attempt | Final outcome 422, network findings, no delivery |
| Infinite loop | Final outcome 422, 10-second script deadline, no delivery |
| Memory exhaustion | Final outcome 422, OOMKilled at the Pod limit, no delivery |
| Tampered signature | Immediate HTTP 401, no EKS admission |
| Replay | First submission delivered; duplicate returned HTTP 401 |

Accepted submissions returned HTTP 202; completed polls returned HTTP 200 with
the final outcome in `status_code`. Seven of seven pipeline scenarios passed.
These sequential checks do not establish sustained reliability or throughput.
Machine-readable results are in [verification-2026-11-12.json](verification-2026-11-12.json).
Detailed local logs are under the gitignored `artifacts/review-2026-11-12/`.


## Merge update 16 November 2026

`main` removed two obsolete test modules, the tracked paper copy, the EKS
implementation plan, and the team handoff. The post-merge non-Docker suite still passes 151 tests. The earlier full
160-test result above describes the pre-merge revision.
The reviewed paper (`Group15_benchmarked_EKS_updated.docx`) is stored outside
this checkout and is not part of this repository.
The new `instructions.pdf` is in the repository root. The current post-merge
full suite needs Docker Desktop for nine legacy tests.
