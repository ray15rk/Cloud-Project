import hmac
import hashlib
import time
import threading
from dataclasses import dataclass, field
from src.watchdog.sign import build_mac_input

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

    # Directly shares one lock object across every NonceStore instance created, since dataclass field defaults are evaluated at class definition time, not at instance creation time
    # default_factory calls threading.Lock() to create a new lock for each instance of NonceStore, ensuring that each instance has its own lock
    # repr=False, compare=False is set since a Lock object has no meaningful string representation and or equality check
    # It is excluded from autogerated __repr__ and __eq__ to avoid confusion and potential issues when comparing NonceStore instances or printing them
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, init=False) 

    # Returns true if nonce has been seen before and record it for future checks
    # Returns false if nonace has been seen and has not expired yet
    def check_and_record(self, nonce: str) -> bool:
        # Run block as one atomic unit
        # A second thread calling this function blocks at this line until first thread entire sequence has finished
        with self._lock:
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
# Returns the payload only if verified successfully, otherwise raises a VerificationError
# Only checks the payload, does not execute it
def verify_payload(message: dict, secret: bytes, nonce_store: NonceStore) -> bytes:
    payload = message["payload"]
    nonce = message["nonce"]
    timestamp = message["timestamp"]
    tag = message["tag"]

    # 1. Authenticity first: recompute the tag over payload + nonce + timestamp
    # Nothing touches the nonce store until we know the message came from someone holding the key
    expected_tag = hmac.new(secret, build_mac_input(payload, nonce, timestamp), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_tag, tag):
        raise VerificationError("HMAC Verification Failed")

    # 2. Freshness: reject anything signed longer ago than the nonce store remembers
    # The timestamp is covered by the HMAC, so an attacker cannot edit it to look fresh
    if abs(time.time() - timestamp) > nonce_store.ttl_seconds:
        raise VerificationError("Message Expired")

    # 3. Only now consume the nonce
    if not nonce_store.check_and_record(nonce):
        raise VerificationError("Replay Attack Detected")

    return payload
