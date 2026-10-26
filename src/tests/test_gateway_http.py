import base64
import time
import uuid
import requests
import json
import hmac
import hashlib
import os
from dotenv import load_dotenv

load_dotenv()
SHARED_SECRET = os.environ.get("SHARED_SECRET", "").encode("utf-8")

def send_to_gateway(language: str, script_content: str):
    payload_bytes = script_content.encode("utf-8")
    payload_b64 = base64.b64encode(payload_bytes).decode("utf-8")
    
    nonce = uuid.uuid4().hex
    timestamp = int(time.time())
    
    # MATCH sign.py EXACTLY: payload_bytes + "|" + nonce + "|" + timestamp
    message_to_sign = payload_bytes + b"|" + nonce.encode("utf-8") + b"|" + str(timestamp).encode("utf-8")
    
    tag = hmac.new(SHARED_SECRET, message_to_sign, hashlib.sha256).hexdigest()
    
    payload = {
        "payload_b64": payload_b64,
        "nonce": nonce,
        "timestamp": timestamp,
        "tag": tag,  
        "language": language
    }
    
    print(f"--- Testing {language.upper()} via HTTP ---")
    try:
        response = requests.post("http://127.0.0.1:8000/submit", json=payload)
        print(f"Status Code: {response.status_code}")
        print(json.dumps(response.json(), indent=2), "\n")
    except requests.exceptions.ConnectionError:
        print("Error: Could not connect to gateway.\n")

    time.sleep(1)

if __name__ == "__main__":
    send_to_gateway("bash", '#!/bin/bash\necho "Bash successful!"')
    send_to_gateway("powershell", 'Write-Output "PowerShell successful!"')
    send_to_gateway("lua", 'print("Lua successful!")')
    send_to_gateway("js", 'console.log("Node.js successful!");')
    send_to_gateway("perl", 'print "Perl successful!\\n";')
    send_to_gateway("ruby", 'puts "Ruby successful!"')