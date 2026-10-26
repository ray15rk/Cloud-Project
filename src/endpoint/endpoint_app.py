from flask import Flask, request, jsonify
import subprocess
import tempfile
import sys
import os
import shutil
from pathlib import Path

# Simulate an endpoint device that receives the payload thats verified and dry run tested
app = Flask(__name__)

def find_bash():
    if os.name == 'nt':
        for candidate in [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\msys64\usr\bin\bash.exe",
            shutil.which("sh"),
        ]:
            if candidate and os.path.exists(candidate):
                return candidate
    return shutil.which("bash") or "bash"

def find_powershell():
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell"

@app.get('/health')
def health():
    return {"status": "ok", "service": "endpoint"}

@app.route('/execute', methods=['POST'])
def execute():
    # Execute the payload bytes received from the request
    payload = request.get_data()

    language = request.headers.get("X-Payload-Language", "").lower()
    if not language:
        first_line = payload[:64].split(b'\n')[0].strip()
        if first_line.startswith(b'#!/bin/bash') or first_line.startswith(b'#!/bin/sh'):
            language = "bash"
        elif first_line.startswith(b'#powershell') or b'Write-Output' in payload[:100]:
            language = "powershell"
        else:
            language = "python"

    if language == "bash":
        suffix = ".sh"
    elif language == "powershell":
        suffix = ".ps1"
    else:
        suffix = ".py"

    tmp = tempfile.NamedTemporaryFile(mode='wb', suffix=suffix, delete=False)
    tmp.write(payload)
    tmp.close()
    tmp_path = Path(tmp.name)

    if language == "bash":
        cmd = [find_bash(), str(tmp_path)]
    elif language == "powershell":
        cmd = [find_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(tmp_path)]
    else:
        cmd = [sys.executable, "-X", "utf8", str(tmp_path)]

    try: 
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10  # Set a timeout for the execution to prevent hanging
        )
        return jsonify({
            "executed": True,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"executed": False, "reason": "endpoint execution timed out"}), 504
    except OSError:
        return jsonify({"executed": False, "reason": "endpoint runtime unavailable"}), 503
    finally:
        tmp_path.unlink(missing_ok=True)  # Clean up the temporary file after execution    

if __name__ == '__main__':
    app.run(port=5000)
