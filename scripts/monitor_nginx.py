"""Aggregate new ingress failures; python -m scripts.monitor_nginx [--once].

One metadata-only notification per interval, never one per flood request. The
cursor advances after acknowledgement, so an unavailable inbox is retried.
Run only one monitor per state file. Rotate logs by rename + nginx -s reopen.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import time

from dotenv import load_dotenv
import requests

from src.ingress import NOTIFY_TIMEOUT

ROOT = Path(__file__).resolve().parents[1]


def ingress_failure(event):
    status = event.get("status")
    no_upstream = event.get("upstream_status", "-") in ("", "-")
    if status in (408, 413, 429) and no_upstream:
        return str(status)
    if status in (502, 504) and event.get("upstream_header_time", "-") in ("", "-"):
        return str(status)
    return None


def poll(log_path, state_path, notify_url):
    """Return an acknowledged summary, or None when no new failures exist."""
    log_path, state_path = Path(log_path), Path(state_path)
    if not log_path.exists():
        return None
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    counts, samples = Counter(), []
    with log_path.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        identity = [stat.st_dev, stat.st_ino]
        offset = state.get("offset", 0) if state.get("identity") == identity else 0
        stream.seek(offset if offset <= stat.st_size else 0)
        while True:
            start = stream.tell()
            line = stream.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                stream.seek(start)  # wait for a complete log record
                break
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue  # permits migration from the former text log format
            if not isinstance(event, dict):
                continue
            failure = ingress_failure(event)
            if failure:
                counts[failure] += 1
                if len(samples) < 3:
                    samples.append(str(event.get("request_id", ""))[:64])
        next_state = {"identity": identity, "offset": stream.tell()}
    report = None
    if counts:
        if not notify_url:
            raise ValueError("NOTIFY_URL is required; cursor retained until notifications are configured")
        report = {"stage": "ingress", "reason": f"NGINX failures: {dict(counts)}",
                  "nonce": None, "counts": dict(counts), "sample_request_ids": samples}
        response = requests.post(notify_url, json=report, timeout=NOTIFY_TIMEOUT)
        response.raise_for_status()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(next_state))
    temporary.replace(state_path)
    return report


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=ROOT / "nginx/logs/access.log")
    parser.add_argument("--state", type=Path, default=ROOT / ".data/nginx-monitor.json")
    parser.add_argument("--interval", type=float, default=10)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be positive")
    try:
        while True:
            try:
                report = poll(args.log, args.state, os.environ.get("NOTIFY_URL", ""))
                if report:
                    print(json.dumps(report), flush=True)
            except (OSError, ValueError, requests.RequestException) as error:
                print(f"Ingress monitor could not complete: {type(error).__name__}; will retry", flush=True)
                if args.once:
                    raise SystemExit(1) from error
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
