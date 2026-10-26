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

def send_to_gateway(language: str, script_content: str, test_name: str = None):

    if test_name:
        print(f"\n--- Testing {language.upper()} via HTTP ({test_name}) ---")
    else:
        print(f"\n--- Testing {language.upper()} via HTTP ---")

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
    
    try:
        response = requests.post("http://127.0.0.1:8000/submit", json=payload)
        print(f"Status Code: {response.status_code}")
        print(json.dumps(response.json(), indent=2), "\n")
    except requests.exceptions.ConnectionError:
        print("Error: Could not connect to gateway.\n")

    time.sleep(1)

if __name__ == "__main__":

    print("--- Running Standard Valid Tests ---")
    send_to_gateway("bash", '#!/bin/bash\necho "Bash successful!"')
    send_to_gateway("powershell", 'Write-Output "PowerShell successful!"')
    send_to_gateway("lua", 'print("Lua successful!")')
    send_to_gateway("js", 'console.log("Node.js successful!");')
    send_to_gateway("perl", 'print "Perl successful!\\n";')
    send_to_gateway("ruby", 'puts "Ruby successful!"')



    print("\n--- Running Adversarial Security Tests ---")

    malicious_python = "x = []\nwhile True:\n    x.append(' ' * 10**6)"   # 1. The Memory Hog (Should return oom_killed)
    send_to_gateway("python", malicious_python, "Memory Hog")

    malicious_js = "while(true) {}"   # 2. The CPU Infinite Loop (Should return timeout after 10s)
    send_to_gateway("js", malicious_js, "Infinite Loop")

    malicious_bash = "curl -m 3 -I https://google.com"   # 3. The Network Breach (Should return an execution error or empty result)
    send_to_gateway("bash", malicious_bash, "Network Breach")

    malicious_ruby = "File.write('/sandbox/hacked.txt', 'hacked')"   # 4. The Filesystem Breakout (Should return a Read-Only error)
    send_to_gateway("ruby", malicious_ruby, "Filesystem Breakout")



    print("\n--- Running Advanced Boundary Tests ---")

    # 5. Process Exhaustion (Should return an execution error about resource limits)
    pid_exhaustion = """import multiprocessing, time   
def worker():
    time.sleep(5)
for i in range(100):
    try:
        multiprocessing.Process(target=worker).start()
    except Exception as e:
        print(f"Blocked at process {i}: {e}")
        break
"""
    send_to_gateway("python", pid_exhaustion, "Process Exhaustion")

    # 6. State Contamination (Write Phase)
    send_to_gateway("bash", "echo 'ghost data' > /tmp/ghost.txt && echo 'Wrote to /tmp'", "State Contamination - Write")
    
    # 7. State Contamination (Read Phase - Should fail to find the file)
    send_to_gateway("bash", "cat /tmp/ghost.txt || echo 'File not found'", "State Contamination - Read")