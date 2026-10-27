import time
import pytest
from src.watchdog.sign import sign_payload
from src.watchdog.verifier import verify_payload, VerificationError, NonceStore

# Testing secret key
SECRET_KEY = b'supersecretkey'

def test_valid_verified_payload():
    # Sign a payload, verify it, and return the unmodified payload
    payload = b"This is a test payload" 
    message = sign_payload(payload, SECRET_KEY)
    result = verify_payload(message, SECRET_KEY, NonceStore())
    assert result == payload

def test_tampered_payload():
    # Mutate the payload after signing and expect a verification failure
    payload = b"This is a test payload"
    message = sign_payload(payload, SECRET_KEY)

    # Tamper with the payload
    message["payload"] = b"This is a tampered payload"

    # With block is expected to raise a VerificationError due to the tampered payload
    # If it dosent raise an error, the test will fail, indicating that the verification process did not catch the tampering
    # Match checks the exeption message to ensure it is the expected error type, ensuring that it fails for the correct reason
    with pytest.raises(VerificationError, match = "HMAC Verification Failed"):
        verify_payload(message, SECRET_KEY, NonceStore())

def test_replayed_nonce():
    # NonceStore check 
    payload = b"This is a test payload"
    message = sign_payload(payload, SECRET_KEY)
    # Single NonceStore instance is used to track seen nonces across multiple verification attemptss
    store = NonceStore()

    # First verification should pass
    verify_payload(message, SECRET_KEY, store)

    # Second delivery of the same message, same nonce, same valid HMAC should be rejcted as a replay attack
    with pytest.raises(VerificationError, match = "Replay Attack Detected"):
        verify_payload(message, SECRET_KEY, store)

def test_wrong_secret():
    # Distinguishes HMAC from bare checksum
    # Payload is completely unmodified but receiver is using different secret than the sender = HMAC mismatch
    payload = b"This is a test payload"
    message = sign_payload(payload, SECRET_KEY)

    # Use a different secret for verification
    wrong_secret = b'wrongsecretkey'

    # Expect a verification failure due to HMAC mismatch
    with pytest.raises(VerificationError, match = "HMAC Verification Failed"):
        verify_payload(message, wrong_secret, NonceStore())

def test_tampered_timestamp():
    # Attacker changes the timestamp to make an old message look fresh
    # Timestamp is covered by the HMAC, so the edit must break the tag
    message = sign_payload(b"This is a test payload", SECRET_KEY)
    message["timestamp"] += 30
    with pytest.raises(VerificationError, match = "HMAC Verification Failed"):
        verify_payload(message, SECRET_KEY, NonceStore())

def test_expired_message():
    # Genuine message signed 2 minutes ago, store only remembers 60s, so it must be rejected
    old_time = int(time.time()) - 120
    message = sign_payload(b"This is a test payload", SECRET_KEY, timestamp = old_time)
    with pytest.raises(VerificationError, match = "Message Expired"):
        verify_payload(message, SECRET_KEY, NonceStore(ttl_seconds = 60))

def test_forged_message_does_not_burn_nonce():
    # Attacker copies a real nonce onto junk and sends it first
    # The junk must fail without recording the nonce, so the genuine message still gets through
    store = NonceStore()
    real = sign_payload(b"This is a test payload", SECRET_KEY)
    forged = {"payload": b"junk", "nonce": real["nonce"], "timestamp": real["timestamp"], "tag": "00"}

    with pytest.raises(VerificationError, match = "HMAC Verification Failed"):
        verify_payload(forged, SECRET_KEY, store)

    assert verify_payload(real, SECRET_KEY, store) == real["payload"]

def test_concurrent_replay():
    # Fire a few simultaneous calls with the same noonce at once and confirm if exactly one is accepted
    import concurrent.futures
    store = NonceStore()
    nonce = "race condition nonce"

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: store.check_and_record(nonce), range(20)))

    assert results.count(True) == 1

def test_changing_language_breaks_the_tag():
    message = sign_payload(b"echo hi", SECRET_KEY, language="bash")
    message["language"] = "powershell"
    with pytest.raises(VerificationError, match="HMAC Verification Failed"):
        verify_payload(message, SECRET_KEY, NonceStore())

def test_signed_language_verifies():
    message = sign_payload(b"echo hi", SECRET_KEY, language="bash")
    assert verify_payload(message, SECRET_KEY, NonceStore()) == b"echo hi"

def test_missing_language_is_treated_as_python():
    message = sign_payload(b"print(1)", SECRET_KEY)
    del message["language"]
    assert verify_payload(message, SECRET_KEY, NonceStore()) == b"print(1)"
