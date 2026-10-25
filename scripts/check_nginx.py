"""Verify the NGINX ingress in front of the gateway.   python -m scripts.check_nginx

Start the stack first (see readme.md): central server, endpoint, `python -m src.gateway.serve`,
then NGINX. Uses the real pipeline, so Docker Desktop must be running and the sandbox image built.
"""
import argparse
import base64
import collections
import os
import json
from pathlib import Path
import statistics
from concurrent.futures import ThreadPoolExecutor
import socket
import sys
import threading
import time
from urllib.parse import urlsplit

import requests
from dotenv import load_dotenv

from src.resource_manager.resource_manager import resource_manager
from src.watchdog.sign import sign_payload
from src.ingress import CLIENT_TIMEOUT

load_dotenv()
SECRET = os.environ.get("SHARED_SECRET", "").encode("utf-8")
results = []


def record(name, ok, detail=""):
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   ({detail})" if detail else ""))


def signed(payload: bytes) -> dict:
    m = sign_payload(payload, SECRET)
    return {"payload_b64": base64.b64encode(payload).decode("ascii"),
            "nonce": m["nonce"], "timestamp": m["timestamp"], "tag": m["tag"]}


def stage_of(response) -> str:
    try:
        body = response.json()
        if not isinstance(body, dict):
            return "?"
        return body.get("stage", "delivered" if body.get("delivered") is True else "?")
    except ValueError:
        return "?"


def check_routes(base):
    bad = []
    for method, path in [("GET", "/"), ("GET", "/api/overview"), ("GET", "/dashboard-assets/dashboard.js"),
                         ("POST", "/api/run"), ("POST", "/api/capacity-test"), ("GET", "/health"), ("POST", "/submit/"), ("POST", "/submit/anything")]:
        status = requests.request(method, base + path, timeout=10).status_code
        if status != 404:
            bad.append(f"{method} {path} -> {status}")
    record("dashboard and every other route are 404 from outside", not bad, "; ".join(bad))
    status = requests.get(base + "/submit", timeout=10).status_code
    record("GET /submit is refused (POST only)", status == 403, f"got {status}")


def check_pipeline(base):
    r = requests.post(base + "/submit", json=signed(b"print('config update applied')"), timeout=60)
    record("valid signed payload is delivered", r.status_code == 200 and stage_of(r) == "delivered", f"HTTP {r.status_code}")

    body = signed(b"print('hello')")
    body["payload_b64"] = base64.b64encode(b"print('tampered')").decode("ascii")
    r = requests.post(base + "/submit", json=body, timeout=60)
    record("tampered payload is dropped at verify", r.status_code == 401 and stage_of(r) == "verify", f"HTTP {r.status_code}")

    body = signed(b"print('once only')")
    first = requests.post(base + "/submit", json=body, timeout=60)
    second = requests.post(base + "/submit", json=body, timeout=60)
    record("replayed payload: delivered once, then rejected", first.status_code == 200 and second.status_code == 401,
           f"HTTP {first.status_code} then {second.status_code}")

    r = requests.post(base + "/submit", json={"junk": "x" * 300_000}, timeout=30)
    record("body over 256k is refused by NGINX (413)", r.status_code == 413, f"got {r.status_code}")


def check_capacity(base):
    slots = resource_manager.snapshot()["slots"]
    n = slots + 2
    probe = b'import time\ntime.sleep(2)\nprint("probe")\n'
    barrier, out = threading.Barrier(n), [None] * n

    def fire(i):
        try:
            body = signed(probe)
            barrier.wait(timeout=10)
            r = requests.post(base + "/submit", json=body, timeout=CLIENT_TIMEOUT)
            return r.status_code, stage_of(r)
        except (requests.RequestException, threading.BrokenBarrierError) as error:
            return "error", type(error).__name__

    with ThreadPoolExecutor(max_workers=n) as pool:
        out = list(pool.map(fire, range(n)))
    delivered = sum(1 for o in out if o == (200, "delivered"))
    rejected = sum(1 for o in out if o == (503, "capacity"))
    record(f"{n} simultaneous slow payloads: {slots} delivered, 2 rejected at stage 'capacity'",
           delivered == slots and rejected == 2, f"delivered={delivered}, capacity-rejected={rejected}")


def check_flood(base):
    n = 60
    barrier = threading.Barrier(n)

    def fire(i):
        try:
            barrier.wait(timeout=10)
            r = requests.post(base + "/submit", json={}, timeout=CLIENT_TIMEOUT)
            return r.status_code
        except (requests.RequestException, threading.BrokenBarrierError) as error:
            return type(error).__name__

    with ThreadPoolExecutor(max_workers=n) as pool:
        outcomes = list(pool.map(fire, range(n)))
    counts = collections.Counter(outcomes)
    record(f"all {n} flood requests complete; overload rejected before the gateway",
           len(outcomes) == n and sum(counts.values()) == n
           and counts[429] > 0 and counts[400] > 0 and set(counts) <= {400, 429},
           str(dict(counts)))


def check_concurrency(base):
    parts = urlsplit(base)
    sockets = []
    try:
        # Hold complete headers + partial bodies: counts against limit_conn but
        # request buffering prevents these clients from occupying gateway threads.
        for _ in range(8):
            s = socket.create_connection((parts.hostname, parts.port or 80), timeout=3)
            sockets.append(s)
            s.sendall(b"POST /submit HTTP/1.1\r\nHost: x\r\nContent-Length: 1000\r\n\r\n{")
            time.sleep(.08)  # isolate concurrency from the 20r/s rate limit
        response = requests.post(base + "/submit", json={}, timeout=5)
        record("ninth concurrent client is rejected with JSON 429", response.status_code == 429
               and response.json().get("stage") == "ingress", f"HTTP {response.status_code}")
    finally:
        for s in sockets:
            s.close()


def benchmark(base):
    latencies = []
    for _ in range(10):
        start = time.perf_counter()
        r = requests.post(base + "/submit", json=signed(b"print('benchmark')"), timeout=CLIENT_TIMEOUT)
        latencies.append((time.perf_counter() - start) * 1000)
        if r.status_code != 200 or not r.json().get("delivered"):
            record("ten normal signed submissions succeed", False, f"HTTP {r.status_code}")
            return {"completed": len(latencies), "failed_status": r.status_code}
    # A paced malformed-message probe measures ingress admission, not script throughput.
    paced = collections.Counter()
    for _ in range(20):
        start = time.perf_counter()
        paced[requests.post(base + "/submit", json={}, timeout=CLIENT_TIMEOUT).status_code] += 1
        time.sleep(max(0, .1 - (time.perf_counter() - start)))
    # Sustained overload, independent of the simultaneous burst probe.
    def paced_fire(_):
        try:
            return requests.post(base + "/submit", json={}, timeout=CLIENT_TIMEOUT).status_code
        except requests.RequestException as error:
            return type(error).__name__
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = []
        started = time.perf_counter()
        for i in range(80):
            time.sleep(max(0, started + i / 40 - time.perf_counter()))
            futures.append(pool.submit(paced_fire, i))
        sustained = collections.Counter(future.result() for future in futures)
    record("sustained 40r/s overload is bounded and all 80 outcomes are recorded",
           sum(sustained.values()) == 80 and sustained[429] > 0 and sustained[400] > 0
           and set(sustained) <= {400, 429}, str(dict(sustained)))
    metrics = {"signed_samples": 10, "median_ms": round(statistics.median(latencies), 2),
               "p95_ms": round(sorted(latencies)[-1], 2),
               "sequential_requests_per_second": round(10000 / sum(latencies), 2),
               "paced_10rps_statuses": dict(paced), "sustained_40rps_statuses": dict(sustained)}
    record("normal signed traffic and paced 10r/s ingress traffic are admitted",
           paced == {400: 20}, json.dumps(metrics))
    return metrics


def check_stalled_client(base):
    # A client that sends headers and half a body, then goes silent, must be cut off by NGINX
    # (client_body_timeout 5s). NGINX closes the connection and logs a 408; the gateway never sees it.
    parts = urlsplit(base)
    started = time.time()
    with socket.create_connection((parts.hostname, parts.port), timeout=15) as s:
        s.sendall(b"POST /submit HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 1000\r\n\r\n{")
        s.settimeout(12)
        try:
            reply = s.recv(200)
            cut_off = not reply or b" 408 " in reply
        except socket.timeout:
            cut_off = False  # still open after 12s: NGINX did not cut the client off
    elapsed = time.time() - started
    record("stalled client is cut off by NGINX within seconds instead of holding a gateway thread",
           cut_off and elapsed < 10, f"connection closed after {elapsed:.1f}s" if cut_off else "still open after 12s")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--base", default=os.environ.get("NGINX_URL", "http://127.0.0.1:8080"))
    args = parser.parse_args()
    if not SECRET:
        raise SystemExit("SHARED_SECRET is not set in .env")
    try:
        requests.get(args.base + "/", timeout=3)
    except requests.RequestException:
        raise SystemExit(f"Cannot reach NGINX at {args.base}. Start the stack first (readme.md).")
    print(f"Checking the ingress at {args.base}\n")
    results.clear()
    check_routes(args.base)
    check_pipeline(args.base)
    check_capacity(args.base)
    check_concurrency(args.base)
    check_stalled_client(args.base)
    check_flood(args.base)   # last: it spends the rate-limit burst
    time.sleep(1)
    metrics = benchmark(args.base) if args.benchmark else {}
    passed = sum(item["ok"] for item in results)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({"checks": results, "metrics": metrics}, indent=2))
    print(f"\n{passed}/{len(results)} checks passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
