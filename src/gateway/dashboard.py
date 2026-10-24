"""Loopback-only operator dashboard; signing keys never leave the server."""
import base64
import hmac
import os
import secrets
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from flask import Blueprint, jsonify, render_template, request
from src.watchdog.sign import sign_payload

EXAMPLES = {
    "good": ("good.py", "Healthy update", "A configuration update that should be delivered."),
    "infinite_loop": ("infinite_loop.py", "Infinite loop", "The sandbox should stop execution at its deadline."),
    "memory_hog": ("memory_hog.py", "Memory exhaustion", "The sandbox should terminate excessive memory allocation."),
}


def register_dashboard(app, gateway):
    bp = Blueprint("dashboard", __name__, template_folder="templates", static_folder="static", static_url_path="/dashboard-assets")
    token = secrets.token_urlsafe(32)
    run_lock = threading.Lock()
    health_lock = threading.Lock()
    health_cache = {"checked": 0, "value": None}

    @bp.before_request
    def local_operator_only():
        host = urlsplit("http://" + request.host).hostname
        if request.remote_addr not in {"127.0.0.1", "::1"} or host not in {"localhost", "127.0.0.1", "::1"}:
            return jsonify(error="The operator dashboard is available on localhost only."), 403
        if request.method == "POST":
            supplied_token = request.headers.get("X-Dashboard-Token", "")
            if not supplied_token.isascii() or not hmac.compare_digest(supplied_token, token):
                return jsonify(error="Dashboard session expired. Reload this page."), 403

    @bp.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        return response

    @bp.get("/")
    def index():
        return render_template("dashboard.html", dashboard_token=token)

    @bp.get("/api/overview")
    def overview():
        try:
            snapshot = gateway.activity_store.snapshot()
        except Exception:
            app.logger.exception("Activity journal unavailable")
            return jsonify(error="Activity journal unavailable. Check the gateway terminal."), 503
        return jsonify(**snapshot, resources=gateway.resource_manager.snapshot(),
                       cloud={"enabled": gateway.EKS_ENABLED}, configured=bool(gateway.SHARED_SECRET))

    @bp.get("/api/examples")
    def examples():
        root = Path(__file__).resolve().parents[1] / "examples"
        return jsonify(examples=[{"id": key, "name": value[0], "title": value[1], "description": value[2],
                                  "code": (root / value[0]).read_text()} for key, value in EXAMPLES.items()])

    def signed_body(payload):
        signed = sign_payload(payload, gateway.SHARED_SECRET)
        return {"payload_b64": base64.b64encode(payload).decode("ascii"),
                "nonce": signed["nonce"], "timestamp": signed["timestamp"], "tag": signed["tag"]}

    @bp.post("/api/run")
    def run():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("code"), str) or not body["code"].strip():
            return jsonify(error="Add a Python script before running the pipeline."), 400
        try:
            payload = body["code"].encode("utf-8")
        except UnicodeEncodeError:
            return jsonify(error="The script must contain valid UTF-8 text."), 400
        if len(payload) > 65536:
            return jsonify(error="Scripts must be 64 KB or smaller."), 413
        mode, name = body.get("mode", "signed"), body.get("name", "untitled.py")
        if not isinstance(mode, str) or mode not in {"signed", "tamper", "replay"} or not isinstance(name, str) or len(name) > 120:
            return jsonify(error="Invalid submission mode or file name."), 400
        if not gateway.SHARED_SECRET:
            return jsonify(error="Set SHARED_SECRET in .env, then restart the dashboard."), 503
        if not run_lock.acquire(blocking=False):
            return jsonify(error="Another dashboard run is in progress. Wait for it to finish."), 409
        try:
            message = signed_body(payload)
            if mode == "tamper":
                # Preserve size so a valid 64 KB script still tests HMAC rejection,
                # rather than accidentally testing the request-size limit.
                tampered = bytearray(payload)
                tampered[0] ^= 1
                message["payload_b64"] = base64.b64encode(tampered).decode("ascii")
            attempts = [gateway.tracked_submission(message, name or "untitled.py", mode)[2]]
            if mode == "replay":
                attempts.append(gateway.tracked_submission(message, name or "untitled.py", mode)[2])
            return jsonify(attempts=attempts)
        finally:
            run_lock.release()

    @bp.post("/api/capacity-test")
    def capacity_test():
        if not gateway.SHARED_SECRET:
            return jsonify(error="Set SHARED_SECRET in .env, then restart the dashboard."), 503
        if not run_lock.acquire(blocking=False):
            return jsonify(error="Another dashboard run is in progress. Wait for it to finish."), 409
        try:
            slots = gateway.resource_manager.snapshot()["slots"]
            count = min(slots + 2, 10)
            payload = b'import time\ntime.sleep(2)\nprint("Capacity probe completed")\n'
            def probe(number):
                return gateway.tracked_submission(signed_body(payload), f"capacity-probe-{number + 1}.py", "capacity")[2]
            with ThreadPoolExecutor(max_workers=count) as pool:
                attempts = list(pool.map(probe, range(count)))
            return jsonify(attempts=attempts, submitted=count, local_slots=slots)
        finally:
            run_lock.release()

    @bp.get("/api/health")
    def health():
        with health_lock:
            if (request.args.get("refresh") != "1"
                    and time.monotonic() - health_cache["checked"] < 10 and health_cache["value"] is not None):
                return jsonify(health_cache["value"])
            services = {"gateway": {"status": "online" if gateway.SHARED_SECRET else "unconfigured"}}
            for key, url in (("endpoint", gateway.ENDPOINT_URL), ("central", gateway.NOTIFY_URL)):
                if not url:
                    services[key] = {"status": "unconfigured"}
                    continue
                parts = urlsplit(url)
                health_url = urlunsplit((parts.scheme, parts.netloc, "/health", "", ""))
                try:
                    response = requests.get(health_url, timeout=1)
                    data = response.json() if response.ok else None
                    services[key] = {"status": "online" if isinstance(data, dict) and data.get("status") == "ok" else "unavailable"}
                except (requests.RequestException, ValueError):
                    services[key] = {"status": "offline"}
            image = os.environ.get("SANDBOX_IMAGE", "cloudproject-sandbox:dev")
            services["image"] = {"status": "unknown", "name": image}
            try:
                info = subprocess.run(["docker", "info", "--format", "{{.OSType}}"], capture_output=True, text=True, timeout=3)
                services["docker"] = {"status": "online" if info.returncode == 0 and info.stdout.strip() == "linux" else "offline"}
                if services["docker"]["status"] == "online":
                    inspected = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"], capture_output=True, text=True, timeout=3)
                    services["image"]["status"] = "ready" if inspected.returncode == 0 else "missing"
            except (OSError, subprocess.TimeoutExpired):
                services["docker"] = {"status": "offline"}
            if not gateway.EKS_ENABLED:
                services["eks"] = {"status": "disabled"}
            else:
                try:
                    services["eks"] = {"status": "online" if gateway.configured_runner().healthy() else "unavailable"}
                except Exception:
                    services["eks"] = {"status": "unavailable"}
            health_cache.update(checked=time.monotonic(), value=services)
            return jsonify(services)

    app.register_blueprint(bp)
