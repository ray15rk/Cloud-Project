import hmac
import hashlib
import secrets
import time

# Generate a nonce (One time use number) for signing requests
def generate_nonce() -> str:
    return secrets.token_hex(16)  # Generate a random 16-byte hex string as nonce

# Build the exact bytes that get signed
# Shared by sign.py and verifier.py so both sides always compute the HMAC over the same input
SUPPORTED_LANGUAGES = frozenset({"python", "bash", "powershell"})

def build_mac_input(payload: bytes, nonce: str, timestamp: int, language: str = "python") -> bytes:
    # language is signed too, so an on-path attacker cannot change which interpreter runs the code
    return (payload + b"|" + nonce.encode("utf-8") + b"|" + str(timestamp).encode("utf-8")
            + b"|" + language.encode("utf-8"))

def sign_payload(payload: bytes, secret: bytes, nonce: str = None, timestamp: int = None,
                 language: str = "python") -> dict:
    if nonce is None:
        nonce = generate_nonce()
    if timestamp is None:
        timestamp = int(time.time())
    tag = hmac.new(secret, build_mac_input(payload, nonce, timestamp, language), hashlib.sha256).hexdigest()
    return {"payload": payload, "nonce": nonce, "timestamp": timestamp,
            "language": language, "tag": tag}
