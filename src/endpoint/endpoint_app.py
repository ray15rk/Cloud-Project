from flask import Flask, request, jsonify
import subprocess
import tempfile
import sys
from pathlib import Path

# Simulate an endpoint device that receives the payload thats verified and dry run tested
# Only execution of the payload is done here since the verification and dry run testing is done in the endpoint_verifier.py file
app = Flask(__name__)

@app.get('/health')
def health():
    return {"status": "ok", "service": "endpoint"}

@app.route('/execute', methods=['POST'])
def execute():
    # Execute the payload bytes received from the request
    payload = request.get_data()

    tmp = tempfile.NamedTemporaryFile(mode='wb', suffix='.py', delete=False)
    tmp.write(payload)
    tmp.close()
    tmp_path = Path(tmp.name)

    try: 
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(tmp_path)],
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

