# Changelog

## 2026-10-22 — Documentation reconciliation

- Reorganized the README around setup, the one-command dashboard, configuration,
  original CLI usage, verification, and remaining AWS work.
- Standardized Windows commands to use the virtual environment directly and added
  macOS/Linux guidance. Corrected the `env.example` name and preserved existing
  `.env` configuration in the setup instructions.
- Removed stale statements about running the base image directly, needing code
  edits to test capacity, or having no endpoint timeout.
- Documented runtime limits, retained history, drafts, best-effort notifications,
  and the difference between a dashboard request and its individual pipeline results.
- Reconciled historical test-stub notes and consolidated outstanding AWS tasks.
  Python remains the implemented runtime; other languages are not implemented.
- Documentation-only validation checked local links, command targets, implementation
  consistency, whitespace, and conflict markers. The recorded 62-test and browser
  results below are from the preceding implementation verification, not a new
  application test run for these documentation edits.

## 2026-10-21 — Local dashboard, reusable image, and regression fixes

### Added

- Relay dashboard on the existing Flask gateway at `http://127.0.0.1:8000`, with
  responsive overview, workbench, activity, and system status views.
- UTF-8 Python editing, `.py` uploads/drag-and-drop, built-in samples, and signed,
  tampered, and replayed submissions. Traces show actual stage outcomes, timings,
  HTTP result codes, and endpoint output or failure reasons.
- SQLite activity history for dashboard and CLI/API submissions, search/filters,
  JSON details, clipboard actions, recent failure summaries, and CSV export with
  spreadsheet-formula escaping. Up to 2,000 records are retained; the latest 100
  are displayed/exportable. Stored stdout/stderr are bounded; drafts use browser
  local storage.
- Local resource snapshots, service/image health checks, manual refresh, and a
  capacity test. With defaults, six probes admit four local dry runs and reject
  two at stage `capacity` when no other submissions are running.
- `python -m src.dashboard` launcher for all three local services, with localhost
  binding, port-conflict handling, and Ctrl+C shutdown.
- `docker/sandbox/Dockerfile` and restricted build context for
  `cloudproject-sandbox:dev`, based on `python:3.11-slim-bookworm`. The image runs
  as UID/GID 65534 and executes separately mounted scripts. `SANDBOX_IMAGE` selects
  it; runtime flags retain the existing isolation protections.
- `docs/aws-setup.md` and `scripts/check-aws.ps1` for Learner Lab credentials and
  read-only identity/ECR/EKS checks. No AWS provisioning or execution is implemented.
- Dashboard/endpoint regression tests, optional Playwright dependencies in
  `requirements-dev.txt`, and `scripts/check_dashboard.py` for real HTTP/Docker and
  desktop/mobile checks using temporary history and separate ports.
- Gitignore entries for local activity data and generated browser artifacts.

### Fixed and hardened

- Restricted dashboard access to loopback hosts and required a page token before
  signing/submission actions. Signing keys stay on the server. Added response
  security headers and script/request-size validation.
- Preserved UTF-8 output in Docker capture and endpoint execution on Windows.
  The endpoint uses the active Python interpreter, returns stdout/stderr and exit
  status, handles timeout/runtime failures, and removes its temporary script.
- Rejected malformed signatures, non-ASCII operator tokens, invalid UTF-8/form data,
  and oversized scripts without unintended HTTP 500s. Tampering preserves payload
  size so a 64 KB script still tests HMAC rejection.
- Recorded timings on verification and unavailable-sandbox failure paths. Invalid
  endpoint JSON or unsuccessful execution cannot be reported as successful delivery.
- Reported unexpected health response shapes as unavailable; manual refresh bypasses
  the health cache.
- Closed journal connections explicitly for Windows cleanup, isolated test history
  from user activity, and fixed narrow-screen table overflow.
- Reset rejected upload selections and notification accessibility state on Escape.
- Corrected the remaining `broken_dry_run` test stub to accept `memory_limit`, so
  the unavailable-sandbox test raises its intended error rather than an argument
  mismatch. The earlier changelog overstated completion of both test-stub fixes.

### Verified

- Full regression suite: **62 tests passed**, including real Docker sandbox tests.
- Original sender CLI: delivery, tamper, and replay over real HTTP; central reception
  of failure metadata was asserted.
- Browser checks: valid execution, timeout/OOM isolation, Unicode, uploads and
  drag-and-drop, invalid/oversized files, empty-editor handling, traces, clipboard,
  capacity, controlled service failures, notifications, search/filters, CSV export,
  details, and history after reload.
- Desktop, 390 px, and 768 px layouts passed without page overflow or uncaught
  browser errors. The one-command launcher was smoke-tested; no sandbox containers
  remained after verification.
- ECR publication, EKS, node autoscaling, and IAM retry execution were not tested
  against AWS and remain pending.

## 2026-10-12 — Local admission and replay concurrency

- Added a mutex to `NonceStore.check_and_record()` for atomic concurrent replay
  detection, with coverage in `test_watchdog.py`.
- Added the lock-protected, memory-based `ResourceManager` between verification and
  dry run. Exhaustion fails closed with HTTP 503 at stage `capacity`.
- Added admission/release and concurrent reservation tests; manually checked the
  running gateway's capacity-rejection path with a reduced budget.
- Corrected the capacity branch's `(response, status_code)` return and tightened
  gateway assertions so a sandbox failure cannot pass as a capacity failure.
  Test-double keyword compatibility received its remaining fix on 2026-10-21.

## Initial local pipeline

- Added HMAC-SHA256 over payload, nonce, and timestamp, constant-time comparison,
  replay rejection, freshness checks, and watchdog tests.
- Added disposable Docker dry runs with CPU/memory restrictions, execution deadline,
  OOM-result handling, no network, non-root execution, read-only mounts/filesystem,
  dropped capabilities, no-new-privileges, and a process limit.
- Added the gateway, endpoint, central notification server, sender CLI
  (`--tamper` / `--replay`), and Python examples.
- Forwarded only the original verified payload after a successful dry run. The
  endpoint executes on the host with a 10-second timeout and no sandbox CPU/memory
  caps; gateway failures trigger a central notification attempt.
- Added isolated watchdog/sandbox/gateway tests and manually exercised local success,
  tamper, replay, infinite-loop, and memory-exhaustion scenarios.

## Remaining work

- ECR publication and EKS provisioning, required roles, network-policy enforcement,
  bounded worker capacity, and node autoscaling.
- Deployment-time Auto Scaling service-linked role setup/verification and bounded
  infrastructure retries for IAM propagation/readiness. Failed payload execution
  must not be retried automatically.
- Disposable hardened EKS Jobs, result collection/cleanup, and gateway overflow
  integration.
- Real two-local/one-cloud concurrency demonstration, cloud-transition and overload
  measurements, evaluation graphs, and cloud-side capacity/cost boundaries.

The local capacity test is complete; it proves admission and safe rejection, not
cloud bursting. See [README](readme.md#remaining-aws-work) and
[AWS setup](docs/aws-setup.md) for the current continuation path.
