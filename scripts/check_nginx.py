"""Verify the NGINX ingress in front of the gateway.   python -m scripts.check_nginx

Start the stack first (see docs/nginx-todo.md): central server, endpoint, `python -m src.gateway.serve`,
then NGINX. Uses the real pipeline, so Docker Desktop must be running and the sandbox image built.
"""
import argparse
import base64
import collections
import os
import socket
import sys
import threading
import time
from urllib.parse import urlsplit

import requests
from dotenv import load_dotenv

from src.resource_manager.resource_manager import resource_manager
from src.watchdog.sign import sign_payload

load_dotenv()
SECRET = os.environ.get("SHARED_SECRET", "").encode("utf-8")
results = []


def record(name, ok, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   ({detail})" if detail else ""))


def signed(payload: bytes) -> dict:
    m = sign_payload(payload, SECRET)
    return {"payload_b64": base64.b64encode(payload).decode("ascii"),
            "nonce": m["nonce"], "timestamp": m["timestamp"], "tag": m["tag"]}


def stage_of(response) -> str:
    try:
        return response.json().get("stage", "delivered")
    except ValueError:
        return "?"


def check_routes(base):
    bad = []
    for method, path in [("GET", "/"), ("GET", "/api/overview"), ("GET", "/dashboard-assets/dashboard.js"),
                         ("POST", "/api/run"), ("POST", "/api/capacity-test"), ("GET", "/health")]:
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
        body = signed(probe)
        barrier.wait()  # all requests leave at the same moment
        r = requests.post(base + "/submit", json=body, timeout=90)
        out[i] = (r.status_code, stage_of(r))

    threads = [threading.Thread(target=fire, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    delivered = sum(1 for o in out if o == (200, "delivered"))
    rejected = sum(1 for o in out if o == (503, "capacity"))
    record(f"{n} simultaneous slow payloads: {slots} delivered, 2 rejected at stage 'capacity'",
           delivered == slots and rejected == 2, f"delivered={delivered}, capacity-rejected={rejected}")


def check_flood(base):
    n, codes, lock = 60, [], threading.Lock()
    barrier = threading.Barrier(n)

    def fire():
        barrier.wait()
        code = requests.post(base + "/submit", json={}, timeout=30).status_code
        with lock:
            codes.append(code)

    threads = [threading.Thread(target=fire) for _ in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    counts = collections.Counter(codes)
    record(f"flood of {n} requests: NGINX answers 429 beyond the burst, the rest reach the gateway",
           counts[429] > 0 and counts[400] > 0, str(dict(counts)))


def check_stalled_client(base):
    # A client that sends headers and half a body, then goes silent, must be cut off by NGINX
    # (client_body_timeout 5s). NGINX closes the connection and logs a 408; the gateway never sees it.
    parts = urlsplit(base)
    started = time.time()
    with socket.create_connection((parts.hostname, parts.port), timeout=15) as s:
        s.sendall(b"POST /submit HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 1000\r\n\r\n{")
        s.settimeout(12)
        try:
            s.recv(200)  # b"" = NGINX closed the connection; any text = it replied (e.g. 408)
            cut_off = True
        except socket.timeout:
            cut_off = False  # still open after 12s: NGINX did not cut the client off
    elapsed = time.time() - started
    record("stalled client is cut off by NGINX within seconds instead of holding a gateway thread",
           cut_off and elapsed < 10, f"connection closed after {elapsed:.1f}s" if cut_off else "still open after 12s")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.environ.get("NGINX_URL", "http://127.0.0.1:8080"))
    args = parser.parse_args()
    if not SECRET:
        raise SystemExit("SHARED_SECRET is not set in .env")
    try:
        requests.get(args.base + "/", timeout=3)
    except requests.RequestException:
        raise SystemExit(f"Cannot reach NGINX at {args.base}. Start the stack first (docs/nginx-todo.md).")
    print(f"Checking the ingress at {args.base}\n")
    check_routes(args.base)
    check_pipeline(args.base)
    check_capacity(args.base)
    check_stalled_client(args.base)
    check_flood(args.base)   # last: it spends the rate-limit burst
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
