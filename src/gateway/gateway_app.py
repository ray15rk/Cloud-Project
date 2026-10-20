# Pipeline gateway; Responsible for receiving payloads, verifying HMAC, and forwarding to sandbox for dry run
# Does this: verify HMAC (watchdog)  -> dry run (sandbox)  ->  forward to endpoint
# Any rejection drops the payload (omission failure) and notifies the central server

import base64
import binascii
import os
import time
import sys
import uuid
from pathlib import Path
 
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
 
from src.sandbox.sandbox import dry_run
from src.watchdog.verifier import NonceStore, VerificationError, verify_payload
from src.resource_manager.resource_manager import resource_manager
from src.gateway.activity import ActivityStore
from src.gateway.dashboard import register_dashboard

load_dotenv()  # Reads SHARED_SECRET etc.

SHARED_SECRET = os.environ.get("SHARED_SECRET", "").encode("utf-8")
ENDPOINT_URL = os.environ.get("ENDPOINT_URL", "http://127.0.0.1:5000/execute")
NOTIFY_URL = os.environ.get("NOTIFY_URL", "")  # Central server's failure inbox

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024
activity_store = ActivityStore(Path(__file__).resolve().parents[2] / ".data" / "activity.sqlite3")
 
# One store shared by every request, so a nonce seen on one request is remembered for the next
nonce_store = NonceStore()

# Lightweight failure notification so a dropped payload is not silent
def notify_failure(stage: str, reason: str, nonce: str | None) -> None:
    # Only metadata is sent, never the payload itself
    report = {"stage": stage, "reason": reason, "nonce": nonce}
    print(f"[gateway] DROPPED at {stage}: {reason} (nonce={nonce})")
    if not NOTIFY_URL:
        return
    try:
        requests.post(NOTIFY_URL, json=report, timeout=2)
    except requests.RequestException as e:
        # A broken notification channel must not crash the gateway or change the decision
        print(f"[gateway] could not notify central server: {e}")

# For running the full pipeline. returns response_json & http_status
# Kept separate from the Flask route so tests can call it directly
def process_submission(body: dict) -> tuple[dict, int]:
    timings = {}
    nonce = body.get("nonce") if isinstance(body, dict) else None
 
    # Initial stage: parse data. JSON cannot carry raw bytes, so the payload travels base64 encoded
    try:
        message = {
            "payload": base64.b64decode(body["payload_b64"], validate=True),
            "nonce": body["nonce"],
            "timestamp": int(body["timestamp"]),
            "tag": body["tag"],
        }
        if (not isinstance(message["nonce"], str) or not 1 <= len(message["nonce"]) <= 128
                or not isinstance(message["tag"], str) or len(message["tag"]) != 64
                or any(character not in "0123456789abcdefABCDEF" for character in message["tag"])
                or len(message["payload"]) > 65536):
            raise ValueError("invalid message fields")
    except (KeyError, TypeError, ValueError, OverflowError, binascii.Error) as e:
        notify_failure("parse", f"malformed message: {e}", nonce)
        return {"delivered": False, "stage": "parse", "reason": "malformed message"}, 400
 
    # Stage 1: integrity verification. Always runs first and locally cuz its cheap.
    if not SHARED_SECRET:
        notify_failure("verify", "gateway secret is not configured", nonce)
        return {"delivered": False, "stage": "verify", "reason": "gateway secret is not configured"}, 503
    t = time.perf_counter()
    try:
        payload = verify_payload(message, SHARED_SECRET, nonce_store)
        
    except VerificationError as e:
        timings["verify_ms"] = round((time.perf_counter() - t) * 1000, 2)
        notify_failure("verify", str(e), nonce)
        return {"delivered": False, "stage": "verify", "reason": str(e), "timings_ms": timings}, 401
    timings["verify_ms"] = round((time.perf_counter() - t) * 1000, 2)

    # Stage 2a: capacity check that gates the dry run specifically, not the whole request
    # NOTE: If the local host has no room, failed closed: EKS bursting not setup, drop it first rather than overcommit
    DRY_RUN_MEMORY_LIMIT = "128m"
    if not resource_manager.try_admit(DRY_RUN_MEMORY_LIMIT):
        notify_failure("capacity", "local host at capacity", nonce)
        return {"delivered": False, "stage":"capacity", "reason":"local host at capacity", "timings_ms": timings}, 503
 
    # Stage 2b: resource-bounded dry run. Since this is expensive, it only runs for legit payloads.
    t = time.perf_counter()
    try:
        result = dry_run(payload, memory_limit=DRY_RUN_MEMORY_LIMIT)

    except Exception as e:
        # Fail closed: if the sandbox itself is broken (e.g. Docker not running), the payload is dropped, never forwarded untested
        print(f"[gateway] dry_run failed unexpectedly: {e}")
        timings["dry_run_ms"] = round((time.perf_counter() - t) * 1000, 2)
        notify_failure("dry_run", f"sandbox unavailable: {e}", nonce)
        return {"delivered": False, "stage": "dry_run", "reason": "sandbox unavailable", "timings_ms": timings}, 503
    
    finally:
        # Container is gone by the time dry_run() returns = success, failure or exception. Release slot unconditioanlly
        resource_manager.release(DRY_RUN_MEMORY_LIMIT)
    timings["dry_run_ms"] = round((time.perf_counter() - t) * 1000, 2)

    if result["status"] != "OK":
        reason = f'{result["status"]}: {result.get("detail", "")}'.strip(": ")
        notify_failure("dry_run", reason, nonce)
        return {"delivered": False, "stage": "dry_run", "reason": reason, "timings_ms": timings}, 422
 
    # Stage 3: forward the ORIGINAL verified payload to the endpoint
    t = time.perf_counter()
    try:
        resp = requests.post(
            ENDPOINT_URL, data=payload,
            headers={"Content-Type": "application/octet-stream"}, timeout=15,
        )
        resp.raise_for_status()
        endpoint_response = resp.json()
        if not isinstance(endpoint_response, dict) or not endpoint_response.get("executed") or endpoint_response.get("returncode") != 0:
            raise ValueError("endpoint did not execute the script successfully")
    except (requests.RequestException, ValueError) as e:
        timings["forward_ms"] = round((time.perf_counter() - t) * 1000, 2)
        notify_failure("forward", f"endpoint unreachable: {e}", nonce)
        return {"delivered": False, "stage": "forward", "reason": "endpoint unreachable", "timings_ms": timings}, 502
    timings["forward_ms"] = round((time.perf_counter() - t) * 1000, 2)
 
    return {"delivered": True, "endpoint_response": endpoint_response, "timings_ms": timings}, 200


def tracked_submission(body, name="CLI submission.py", scenario="external"):
    started = time.perf_counter()
    response, status = process_submission(body)
    event = {"id": uuid.uuid4().hex, "name": name, "scenario": scenario,
             "created_at": time.time(), "status_code": status,
             "duration_ms": round((time.perf_counter() - started) * 1000, 2), "response": response}
    try:
        event = activity_store.record(event)
    except Exception:
        app.logger.exception("Could not save pipeline activity")
        event["journal_error"] = "Result could not be saved to activity history."
    return response, status, event


register_dashboard(app, sys.modules[__name__])


@app.errorhandler(413)
def request_too_large(error):
    return jsonify(error="Request too large. Scripts must be 64 KB or smaller."), 413

@app.route("/submit", methods=["POST"])
def submit():
    body = request.get_json(silent=True)  # None instead of an exception if the body isn't JSON
    response, status, _ = tracked_submission(body or {})
    return jsonify(response), status
 
 
if __name__ == "__main__":
    if not SHARED_SECRET:
        raise SystemExit("SHARED_SECRET is not set. Copy .env.example to .env and fill it in.")
    app.run(port=8000)
