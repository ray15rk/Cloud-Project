import hmac
import hashlib
import secrets

# Generate a nonce (One time use number) for signing requests
def generate_nonce() -> str:
    return secrets.token_hex(16) # Generate a random 16-byte hex string as nonce

# Compute an HMAC-SHA256 tag over (payload + nonce), and package everything the receiver will need to verify it
def sign_payload(payload: bytes, secret: bytes, nonce: str = None) -> dict:
    if nonce is None:
        nonce = generate_nonce()

    # Concatenate the payload and nonce to create the input for HMAC
    # Makes it so that the nonce is included in the HMAC computation, ensuring that each signature is unique even for the same payload
    mac_input = payload + nonce.encode("utf-8")
    
    # Compute the HMAC-SHA256 tag using the secret key and the concatenated input
    # Hexdigest is used to convert the binary HMAC output into a hexadecimal string representation
    tag = hmac.new(secret, mac_input, hashlib.sha256).hexdigest()

    # Return a dictionary containing the payload, nonce, and tag
    return {
        "payload": payload,
        "nonce": nonce,
        "tag": tag
    }