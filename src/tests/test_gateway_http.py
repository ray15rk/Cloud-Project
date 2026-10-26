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
    
    # MATCH sign.py: payload_bytes + "|" + nonce + "|" + timestamp
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
    print("==================================================")
    print("--- 1. Standard Valid Executions (Python, Bash, PowerShell) ---")
    print("==================================================")
    send_to_gateway("python", 'print("Python execution successful!")', "Standard Python")
    send_to_gateway("bash", '#!/bin/bash\necho "Bash execution successful!"', "Standard Bash")
    send_to_gateway("powershell", 'Write-Output "PowerShell execution successful!"', "Standard PowerShell")

    print("\n==================================================")
    print("--- 2. Adversarial Security Boundary Tests ---")
    print("==================================================")

    # 1. Memory Hog - Python (Should return oom_killed at 128 MB)
    malicious_python_oom = "x = []\nwhile True:\n    x.append(' ' * 10**6)"
    send_to_gateway("python", malicious_python_oom, "Python Memory Hog")

    # 2. Memory Hog - Bash (Exponential doubling triggering 128 MB cgroup kill)
    malicious_bash_oom = 'data="x"\nwhile true; do\n    data="$data$data"\ndone'
    send_to_gateway("bash", malicious_bash_oom, "Bash Memory Hog")

    # 3. CPU Infinite Loop - Python (Should return timeout after 10s)
    malicious_python_loop = "while True:\n    pass"
    send_to_gateway("python", malicious_python_loop, "Python Infinite Loop")

    # 4. CPU Infinite Loop - Bash (Should return timeout after 10s)
    malicious_bash_loop = "while true; do\n    :\ndone"
    send_to_gateway("bash", malicious_bash_loop, "Bash Infinite Loop")

    # 5. Outbound Network Breach - Bash (Should fail under --network none)
    malicious_bash_net = "curl -m 3 -I https://google.com"
    send_to_gateway("bash", malicious_bash_net, "Bash Network Breach")

    # 6. Filesystem Breakout - Bash (Should fail under --read-only rootfs)
    malicious_bash_fs = "echo 'tampered' > /sandbox/hacked.txt"
    send_to_gateway("bash", malicious_bash_fs, "Bash Filesystem Breakout")

    print("\n==================================================")
    print("--- 3. Advanced Boundary & Ephemeral State Tests ---")
    print("==================================================")

    # 7. Process Exhaustion / Fork Bomb (Cap at --pids-limit 64)
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

    # 8. State Contamination (Write Phase)
    send_to_gateway("bash", "echo 'ghost data' > /tmp/ghost.txt && echo 'Wrote to /tmp'", "State Contamination - Write")
    
    # 9. State Contamination (Read Phase - Should fail to find the file due to --rm + tmpfs)
    send_to_gateway("bash", "cat /tmp/ghost.txt || echo 'File not found'", "State Contamination - Read")
