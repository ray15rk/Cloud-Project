# Changelog

## 2026-10-12
- Added a mutex lock to `NonceStore.check_and_record()` (`src/watchdog/verifier.py`)
  to close a check-then-write race between concurrent requests sharing the
  same nonce. Covered by `test_concurrent_replay` in `src/tests/test_watchdog.py`.
- Added Phase 2 resource manager (`src/resource_manager/resource_manager.py`):
  atomic, lock-protected memory counter gating local dry-run admission.
  Memory-only (not CPU) by design — memory exhaustion risks a host crash
  via the OOM killer, CPU contention just throttles gracefully. Wired
  into `gateway_app.py` between HMAC verification and the dry run, so
  capacity is checked per-payload at the point it's actually needed,
  not for the whole request upfront. Fails closed (HTTP 503, stage
  "capacity") when the local host is full — EKS bursting is not yet
  implemented, so over-capacity payloads are dropped rather than run
  over budget. Tests in `src/tests/test_resource_manager.py`, including
  `test_concurrent_admission`, the same category of concurrency check
  as `test_concurrent_replay` for `NonceStore`.
- Manually verified the capacity-exceeded path through the real running
  gateway (temporarily lowered `total_memory_mb` below a single dry
  run's requirement, confirmed HTTP 503 with stage "capacity" before
  the sandbox was ever reached).
- Fixed a signature mismatch between `gateway_app.py`'s real `dry_run()`
  call (now passes `memory_limit=` as a keyword, added when Phase 2 was
  wired in) and `test_gateway.py`'s `fake_dry_run`/`broken_dry_run`
  mocks, which still only accepted `payload`. The mismatch raised a
  `TypeError` that was silently caught by the gateway's own
  "sandbox unavailable" fail-closed handler, producing a plausible-looking
  503 that masked the real cause. Both fakes now accept
  `memory_limit=None`, matching the real function's signature.
- Fixed the capacity-rejection branch in `process_submission()`
  (`gateway_app.py`), which returned a bare dict instead of the
  `(dict, status_code)` tuple every other branch returns — would have
  crashed `submit()` with `ValueError: too many values to unpack` the
  first time a real capacity rejection occurred. Added the missing
  `, 503`.
- Added an explicit stage assertion to `test_sandbox_crash_fails_closed`
  (`resp.get_json()["stage"] == "dry_run"`) — it previously only checked
  status code 503 and nothing forwarded, which is indistinguishable from
  a capacity rejection and was silently passing for the wrong reason.
- `test_capacity_exceeded_rejected` now passes and genuinely exercises
  the capacity-rejected path (stage "capacity", 503, nothing forwarded,
  central server notified) — confirmed it wasn't accidentally relying
  on the Bug 1 TypeError to produce a matching status code.
- Full `test_gateway.py` suite (10 cases) passing.

## Initial
- HMAC watchdog (Phase 1): `src/watchdog/sign.py` + `verifier.py`. Binds
  payload + nonce + timestamp; constant-time comparison; replay and
  expiry checks. Tests in `src/tests/test_watchdog.py`.
- Resource-bounded dry-run sandbox (Phase 3): `src/sandbox/sandbox.py`.
  Docker-based, CPU/memory limits, execution deadline for CPU-bound
  loops, OOM-kill detection. Hardened: no network, non-root user,
  read-only filesystem, all capabilities dropped, no-new-privileges,
  PID limit. Tests in `src/tests/test_sandbox.py`.
- Endpoint device (`src/endpoint/endpoint_app.py`): receives a verified,
  dry-run-tested payload and executes it for real, with no resource
  limits of its own by design.
- Gateway (`src/gateway/gateway_app.py`): ties watchdog + sandbox +
  endpoint together end to end. Fails closed on any stage failure
  (malformed input, bad HMAC, replay, dry-run failure, sandbox/endpoint
  unreachable) and notifies the central server rather than dropping
  silently. Tests in `src/tests/test_gateway.py` (Docker and the
  endpoint mocked).
- Central server (`src/sender/central_server.py`) and sender CLI
  (`src/sender/send.py`, with `--tamper`/`--replay` flags) for driving
  the pipeline manually.
- Example payloads (`src/examples/`): `good.py`, `infinite_loop.py`,
  `memory_hog.py`.
- Full live pipeline (all four processes, real HTTP, no mocks) manually
  verified: golden path, tamper, replay, infinite loop, memory leak —
  all produced the expected stage/status/HTTP code.


## TODO
**The scripts testing assumes all is in python for now, need to change once all is done.
- EKS cloud-burst path for the over-capacity case — resource manager currently
  fails closed (drops the payload) rather than bursting anywhere, since the
  cluster isn't provisioned yet.

  1. **Provision AWS infrastructure** (not started):
     - Stand up a small EKS cluster (eksctl or Terraform).
     - Push the sandbox's container image to ECR — currently `sandbox.py`
       runs `python:3.11-slim` directly with CLI flags for hardening
       (`--network none`, `--user 65534:65534`, etc.); for EKS this
       hardening needs to move into a Kubernetes Job spec / Pod
       SecurityContext instead of `docker run` flags, since there's no
       Docker CLI on the other side.
     - Create the Auto Scaling Service-Linked Role during this setup
       step specifically — proposal Section 5.3 depends on this
       happening during deployment, not in the request hot path.

  2. **Replace the fail-closed branch in `process_submission()`**
     (`gateway_app.py`) with real Kubernetes Job submission:
     - When `resource_manager.try_admit()` returns `False`, submit a
       Job via the Kubernetes API (`kubernetes` Python client) instead
       of immediately returning 503.
     - Job must mount/receive the same payload, run the same dry-run
       logic as `sandbox.py`'s local path, and report back
       success/oom/timeout the same way `dry_run()` does now — ideally
       sharing code with `sandbox.py` rather than duplicating it, so
       the two execution paths can't silently drift apart.
     - Add the retry-with-backoff from Section 5.3 around the first
       Job submission, to absorb any residual IAM propagation delay.

  3. **Re-test the original motivating scenario for real**, not just in
     unit tests: 3 payloads arriving concurrently, local capacity for
     only 2 — confirm the 3rd actually completes via a real EKS Job,
     not just that it correctly gets *rejected* locally (which is all
     `test_capacity_exceeded_rejected` currently proves).

  4. **Revisit `ResourceManager` once both paths exist**:
     - Currently tracks only local memory. Once EKS is a real option,
       decide whether "local full" should always burst, or whether EKS
       itself needs its own capacity/cost awareness (cluster autoscaling
       limits, cost caps) rather than being treated as infinite capacity.
     - `test_gateway.py`'s mocks will need a fake Kubernetes Job submission
       alongside the existing fake `dry_run`/`requests.post`, following the
       same pattern already used for Docker/endpoint mocking.

  5. **Update `send.py`/example scripts** if testing needs a way to force
     the over-capacity branch deliberately (e.g. a `--force-burst` flag
     or temporarily shrinking `total_memory_mb`), rather than only being
     able to trigger it by hand-editing the gateway like today's manual
     check did.