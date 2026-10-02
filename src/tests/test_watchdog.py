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