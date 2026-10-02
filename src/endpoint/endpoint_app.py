from flask import Flask, request, jsonify
import subprocess
import tempfile
from pathlib import Path

# Simulate an endpoint device that receives the payload thats verified and dry run tested
# Only execution of the payload is done here since the verification and dry run testing is done in the endpoint_verifier.py file
app = Flask(__name__)

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
            ["python", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=10  # Set a timeout for the execution to prevent hanging
        )
        return jsonify({
            "executed": True,
            "stdout": result.stdout,
            "returncode": result.returncode,
        })
    finally:
        tmp_path.unlink(missing_ok=True)  # Clean up the temporary file after execution    

if __name__ == '__main__':
    app.run(port=5000)

