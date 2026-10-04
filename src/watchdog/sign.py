import hmac
import hashlib
import secrets
import time

# Generate a nonce (One time use number) for signing requests
def generate_nonce() -> str:
    return secrets.token_hex(16)  # Generate a random 16-byte hex string as nonce

# Build the exact bytes that get signed
# Shared by sign.py and verifier.py so both sides always compute the HMAC over the same input
def build_mac_input(payload: bytes, nonce: str, timestamp: int) -> bytes:
    # "|" separators stop ambiguity between fields (e.g. nonce "ab"+ts "12" vs nonce "a"+ts "b12")
    return payload + b"|" + nonce.encode("utf-8") + b"|" + str(timestamp).encode("utf-8")

# Compute an HMAC-SHA256 tag over (payload + nonce + timestamp), and package everything the receiver needs
def sign_payload(payload: bytes, secret: bytes, nonce: str = None, timestamp: int = None) -> dict:
    if nonce is None:
        nonce = generate_nonce()
    if timestamp is None:
        timestamp = int(time.time())  # Seconds since epoch, when the message was signed

    tag = hmac.new(secret, build_mac_input(payload, nonce, timestamp), hashlib.sha256).hexdigest()

    return {
        "payload": payload,
        "nonce": nonce,
        "timestamp": timestamp,
        "tag": tag,
    }
