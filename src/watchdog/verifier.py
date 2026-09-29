import hmac
import hashlib
import time
from dataclasses import dataclass, field

# Define a custom exception for verification errors: 
# Raising specifc errors allow checking for specific error types in the calling code 
# Provides more context about what went wrong during verification
# Raised whenever a payload fails the watchdog check (HMAC doesn't match, or because the nonce has been seen before)
class VerificationError(Exception):
    pass 

@dataclass
# Remembers the nonces that have been seen before, and the time they were seen at
# Prevents replay attacks by ensuring that each nonce is only used once within a certain time window
class NonceStore:
    ttl_seconds: int = 60  # Time to live for nonces
    seen: dict = field(default_factory=dict)  # Dictionary to store seen nonces and their timestamps

    # Returns true if nonce has been seen before and record it for future checks
    # Returns false if nonace has been seen and has not expired yet
    def check_and_record(self, nonce: str) -> bool:
        now = time.time()

        # Remove expired nonces from the store to prevent memory bloat and ensure that only recent nonces are kept
        expired = [n for n, t in self.seen.items() if now - t > self.ttl_seconds]
        for n in expired:
            del self.seen[n]  # Remove expired nonces from the store

        if nonce in self.seen:
            return False # Nonce has been seen before 

        self.seen[nonce] = now  # Record the nonce with the current timestamp
        return True  # Nonce is new and has been recorded

# Verifies an incoming payload exactly as it was signed, using the provided secret and nonce store
# Return the payload only if its verified successfully, otherwise raises a VerificationError
# Only checks the payload, does not execute it
def verify_payload(message: dict, secret: bytes, nonce_store: NonceStore) -> bool:
    payload = message["payload"]
    nonce = message["nonce"]
    tag = message["tag"]

    # Check if its a replay attack first before touching HMAC 
    # If HMAC is checked first but it has failed, we still want to have a clear error message
    # Checking the replay attack fist means a valid message is caught for the right reason rather than just failing the HMAC check
    if not nonce_store.check_and_record(nonce):
        raise VerificationError("Replay Attack Detected")

    # Recompute the HMAC-SHA256 tag using the secret key and the concatenated input (payload + nonce)
    # Ensures that if sender and receiver miss on any of the three components (payload, nonce, or secret), the verification will fail
    # Suspect a mismatch here before suspecting a replay attack
    mac_input = payload + nonce.encode("utf-8")
    expected_tag = hmac.new(secret, mac_input, hashlib.sha256).hexdigest()

    # Standard comparison string not used to prevent timing attacks, which could allow an attacker to guess the correct tag byte by byte
    if not hmac.compare_digest(expected_tag, tag):
        raise VerificationError("HMAC Verification Failed")

    # Both check passed
    return payload