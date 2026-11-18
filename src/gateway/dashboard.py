"""Loopback-only operator dashboard; signing keys never leave the server."""
import base64
import hmac
import os
import secrets

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from flask import Blueprint, jsonify, render_template, request
from src.watchdog.sign import sign_payload

EXAMPLES = {
    "good": ("good.py", "Python: Healthy update", "A Python configuration update that should be delivered."),
    "infinite_loop": ("infinite_loop.py", "Python: Infinite loop", "The sandbox should stop execution at its deadline."),
    "memory_hog": ("memory_hog.py", "Python: Memory exhaustion", "The sandbox should terminate excessive memory allocation."),
    "good_sh": ("good.sh", "Bash: Healthy update", "A Bash shell diagnostic script that should be delivered."),
    "infinite_loop_sh": ("infinite_loop.sh", "Bash: Infinite loop", "The sandbox stops execution at its deadline."),
    "memory_hog_sh": ("memory_hog.sh", "Bash: Memory exhaustion", "The sandbox terminates excessive memory allocation."),
    "network_breach_sh": ("network_breach.sh", "Bash: Network breach probe", "Probes outbound network restrictions and Falco detection."),
    "filesystem_breakout_sh": ("filesystem_breakout.sh", "Bash: Filesystem breakout", "Probes the EKS read-only filesystem and Falco detection."),
    "good_ps": ("good.ps1", "PowerShell: Healthy update", "Currently blocked by Falco findings from PowerShell startup."),
}

def register_dashboard(app, gateway):
    bp = Blueprint("dashboard", __name__, template_folder="templates", static_folder="static", static_url_path="/dashboard-assets")
    token = secrets.token_urlsafe(32)
    run_lock = threading.Lock()
    health_lock = threading.Lock()
    health_cache = {"checked": 0, "value": None, "in_progress": False}

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
        return jsonify(**snapshot,
                       cloud={"enabled": gateway.EKS_ENABLED,
                              "active": gateway.cloud_store.active_count(),
                              "limit": gateway.cloud_store.max_inflight},
                       configured=bool(gateway.SHARED_SECRET))

    @bp.get("/api/metrics")
    def metrics():
        try:
            return jsonify(**gateway.activity_store.metrics(),
                           cloud_limit=gateway.cloud_store.max_inflight)
        except Exception:
            app.logger.exception("Pipeline metrics unavailable")
            return jsonify(error="Pipeline metrics unavailable."), 503

    @bp.get("/api/examples")
    def examples():
        root = Path(__file__).resolve().parents[1] / "examples"
        return jsonify(examples=[{"id": key, "name": value[0], "title": value[1], "description": value[2],
                                  "code": (root / value[0]).read_text()} for key, value in EXAMPLES.items()])

    def signed_body(payload, language="python"):
        signed = sign_payload(payload, gateway.SHARED_SECRET, language=language)
        return {"payload_b64": base64.b64encode(payload).decode("ascii"),
                "nonce": signed["nonce"], "timestamp": signed["timestamp"], "tag": signed["tag"], "language": language}

    @bp.post("/api/run")
    def run():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("code"), str) or not body["code"].strip():
            return jsonify(error="Add a script before running the pipeline."), 400
        try:
            payload = body["code"].encode("utf-8")
        except UnicodeEncodeError:
            return jsonify(error="The script must contain valid UTF-8 text."), 400
        
        if len(payload) > 65536:
            return jsonify(error="Scripts must be 64 KB or smaller."), 413
        mode, name = body.get("mode", "signed"), body.get("name", "untitled.py")

        if not isinstance(mode, str) or mode not in {"signed", "tamper", "replay"} or not isinstance(name, str) or len(name) > 120:
            return jsonify(error="Invalid submission mode or file name."), 400
        ext = os.path.splitext(name or "")[1].lower()
        explicit_lang = body.get("language")
        lang = explicit_lang if explicit_lang in {"python", "bash", "powershell"} else ("bash" if ext == ".sh" else ("powershell" if ext == ".ps1" else "python"))
        
        if not gateway.SHARED_SECRET:
            return jsonify(error="Set SHARED_SECRET in .env, then restart the dashboard."), 503
        
        if not run_lock.acquire(blocking=False):
            return jsonify(error="Another dashboard run is in progress. Wait for it to finish."), 409
        try:
            message = signed_body(payload, language=lang)
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
            limit = gateway.cloud_store.max_inflight
            count = min(limit + 2, 10)
            payload = b'import time\ntime.sleep(2)\nprint("Capacity probe completed")\n'
            def probe(number):
                return gateway.tracked_submission(signed_body(payload), f"capacity-probe-{number + 1}.py", "capacity")[2]
            with ThreadPoolExecutor(max_workers=count) as pool:
                attempts = list(pool.map(probe, range(count)))
            return jsonify(attempts=attempts, submitted=count, cloud_limit=limit)
        finally:
            run_lock.release()

    @bp.get("/api/health")
    def health():
        is_refresh = request.args.get("refresh") == "1"
        # Serve from cache without holding the lock during slow HTTP checks.
        with health_lock:
            if (not is_refresh
                    and time.monotonic() - health_cache["checked"] < 10
                    and health_cache["value"] is not None):
                return jsonify(health_cache["value"])
            
            if health_cache.get("in_progress") and not is_refresh and health_cache["value"] is not None:
                return jsonify(health_cache["value"])
            health_cache["in_progress"] = True

        def check_endpoint():
            url = gateway.ENDPOINT_URL
            if not url:
                return "endpoint", {"status": "unconfigured"}
            parts = urlsplit(url)
            health_url = urlunsplit((parts.scheme, parts.netloc, "/health", "", ""))
            try:
                response = requests.get(health_url, timeout=2)
                data = response.json() if response.ok else None
                status = "online" if isinstance(data, dict) and data.get("status") == "ok" else "unavailable"
                return "endpoint", {"status": status}
            except (requests.RequestException, ValueError):
                return "endpoint", {"status": "offline"}
            
        def check_central():
            url = gateway.NOTIFY_URL
            if not url:
                return "central", {"status": "unconfigured"}
            parts = urlsplit(url)
            health_url = urlunsplit((parts.scheme, parts.netloc, "/health", "", ""))
            try:
                response = requests.get(health_url, timeout=2)
                data = response.json() if response.ok else None
                status = "online" if isinstance(data, dict) and data.get("status") == "ok" else "unavailable"
                return "central", {"status": status}
            except (requests.RequestException, ValueError):
                return "central", {"status": "offline"}
            
        def check_cloud():
            if not gateway.EKS_ENABLED:
                return {"eks": {"status": "disabled"}, "falco": {"status": "not_connected"}}
            try:
                runner = gateway.configured_runner()
                if hasattr(runner, "check_health"):
                    eks_ok, falco_ok = runner.check_health()
                else:
                    eks_ok = getattr(runner, "healthy", lambda: False)()
                    falco_ok = getattr(runner, "monitor_healthy", lambda: None)() if gateway.FALCO_ENABLED else None
                eks_status = "online" if eks_ok else "unavailable"
                if not gateway.FALCO_ENABLED or falco_ok is None:
                    falco_status = "not_connected"
                else:
                    falco_status = "online" if falco_ok else "unavailable"
            except Exception:
                eks_status = "unavailable"
                falco_status = "unavailable" if gateway.FALCO_ENABLED else "not_connected"
            return {"eks": {"status": eks_status}, "falco": {"status": falco_status}}
        try:
            services = {"gateway": {"status": "online" if gateway.SHARED_SECRET else "unconfigured"}}
            with ThreadPoolExecutor(max_workers=3) as pool:
                f_endpoint = pool.submit(check_endpoint)
                f_central = pool.submit(check_central)
                f_cloud = pool.submit(check_cloud)
                k1, v1 = f_endpoint.result()
                k2, v2 = f_central.result()
                cloud_results = f_cloud.result()
            services[k1] = v1
            services[k2] = v2
            services.update(cloud_results)
            with health_lock:
                health_cache.update(checked=time.monotonic(), value=services)
            return jsonify(services)
        finally:
            with health_lock:
                health_cache["in_progress"] = False

    app.register_blueprint(bp)
