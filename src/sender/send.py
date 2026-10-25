# Simulates the central management server pushing a script through the gateway
# Usage: python -m src.sender.send path/to/script.py [--tamper] [--replay] 

import argparse
import base64
import json
import os
 
import requests
from dotenv import load_dotenv
 
from src.watchdog.sign import sign_payload
from src.ingress import CLIENT_TIMEOUT, GATEWAY_URL as DEFAULT_GATEWAY_URL

load_dotenv()
SHARED_SECRET = os.environ.get("SHARED_SECRET", "").encode("utf-8")
GATEWAY_URL = os.environ.get("GATEWAY_URL", DEFAULT_GATEWAY_URL)


def response_body(response):
    """Older proxies and infrastructure errors may return HTML or plain text."""
    try:
        return response.json()
    except ValueError:
        return {"delivered": False, "stage": "ingress", "status": response.status_code,
                "reason": response.reason or "Non-JSON HTTP response",
                "request_id": response.headers.get("X-Request-ID")}
 
 
def build_request(payload: bytes) -> dict:
    # Sign the payload, then base64 encode it so it can travel inside JSON
    message = sign_payload(payload, SHARED_SECRET)
    return {
        "payload_b64": base64.b64encode(message["payload"]).decode("ascii"),
        "nonce": message["nonce"],
        "timestamp": message["timestamp"],
        "tag": message["tag"],
    }
 
 
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("script", help="Path to the script to push")
    parser.add_argument("--tamper", action="store_true", help="Corrupt the payload after signing")
    parser.add_argument("--replay", action="store_true", help="Send the same signed message twice")
    args = parser.parse_args()
 
    with open(args.script, "rb") as f:
        body = build_request(f.read())
 
    if args.tamper:
        # Simulate in-transit corruption: change the payload but keep the original tag
        body["payload_b64"] = base64.b64encode(b"print('tampered!')").decode("ascii")
 
    for attempt in range(2 if args.replay else 1):
        try:
            resp = requests.post(GATEWAY_URL, json=body, timeout=CLIENT_TIMEOUT)
        except requests.RequestException as error:
            raise SystemExit(f"Submission failed ({type(error).__name__}). Execution outcome may be unknown; "
                             "check the gateway before resubmitting. No automatic retry was made.") from error
        print(f"--- attempt {attempt + 1}: HTTP {resp.status_code}")
        print(json.dumps(response_body(resp), indent=2))
 
 
if __name__ == "__main__":
    main()
