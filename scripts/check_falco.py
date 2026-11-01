"""Live check of the Falco behaviour monitor against your EKS cluster.   python -m scripts.check_falco

Runs small canary scripts as REAL EKS Jobs (one at a time) and checks the verdict.
Every canary exits 0, so an exit-code check alone would call them all clean. Needs:
  * EKS_IMAGE, EKS_CONTEXT and working AWS/kube credentials (same as the gateway)
  * FALCO_ENABLED=true in .env, with Falco installed as described in docs/falco-setup.md

Options:  --only NAME [NAME ...]   run only these canaries      --json   print machine-readable results
Exit code: 0 when every required canary matched, 1 otherwise.
"""
import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from src.sandbox.eks_runner import configured_runner  # noqa: E402  (after load_dotenv)
from src.sandbox.falco_monitor import monitoring_enabled  # noqa: E402

EXAMPLES = Path(__file__).resolve().parents[1] / "src" / "examples"

# name, example file, language, expected status, expected behaviour class, required?
CANARIES = [
    ("benign",             "good.py",                "python",     "OK",              None,          True),
    ("network_swallowed",  "network_swallowed.py",   "python",     "threat_detected", "network",     True),
    ("exec_child",         "exec_child.py",          "python",     "threat_detected", "process",     True),
    ("fs_write_swallowed", "fs_write_swallowed.py",  "python",     "threat_detected", "filesystem",  True),
    ("credential_probe",   "credential_probe.py",    "python",     "threat_detected", "credentials", True),
    # Informational: seccomp can deny these before Falco's probe sees them, so a miss is a warning.
    ("privileged_syscall", "privileged_syscall.py",  "python",     "threat_detected", "privilege",   False),
    ("benign_bash",        "good.sh",                "bash",       "OK",              None,          False),
    ("benign_powershell",  "good.ps1",               "powershell", "OK",              None,          False),
]


def run_canary(runner, name, filename, language, want_status, want_behaviour, required):
    payload = (EXAMPLES / filename).read_bytes()
    started = time.monotonic()
    try:
        result = runner.run(payload, uuid.uuid4().hex, language)
    except Exception as exc:  # infrastructure problem, not a verdict
        return {"name": name, "ok": False, "required": required, "status": "exception",
                "detail": f"{type(exc).__name__}: {exc}", "seconds": round(time.monotonic() - started, 1)}
    behaviour = result.get("behaviour") or {}
    classes = sorted({finding.get("behaviour") for finding in behaviour.get("findings", [])})
    ok = result.get("status") == want_status and (want_behaviour is None or want_behaviour in classes)
    return {"name": name, "ok": ok, "required": required, "status": result.get("status"),
            "expected": want_status, "behaviours": classes, "expected_behaviour": want_behaviour,
            "observed": behaviour.get("observed"), "detail": (result.get("detail") or "")[:160],
            "monitor_ms": result.get("monitor_ms"), "seconds": round(time.monotonic() - started, 1)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="+", metavar="NAME", help="run only these canaries")
    parser.add_argument("--json", action="store_true", help="print results as JSON")
    args = parser.parse_args()

    if not monitoring_enabled():
        raise SystemExit("FALCO_ENABLED is not true. Set FALCO_ENABLED=true in .env first.")
    chosen = [c for c in CANARIES if not args.only or c[0] in args.only]
    if not chosen:
        raise SystemExit(f"No canary matches {args.only}. Choices: {', '.join(c[0] for c in CANARIES)}")

    runner = configured_runner()
    results = []
    for name, filename, language, want_status, want_behaviour, required in chosen:
        if not args.json:
            print(f"running {name} ...", flush=True)
        results.append(run_canary(runner, name, filename, language, want_status, want_behaviour, required))

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print()
        for r in results:
            mark = "PASS" if r["ok"] else ("FAIL" if r["required"] else "WARN")
            seen = ",".join(r.get("behaviours") or []) or "-"
            print(f"[{mark}] {r['name']:20} status={r['status']:17} behaviours={seen:12} "
                  f"observed={r.get('observed')} {r['seconds']}s")
            if not r["ok"]:
                print(f"       expected {r.get('expected')} / {r.get('expected_behaviour')}: {r['detail']}")
    return 1 if any(not r["ok"] and r["required"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
