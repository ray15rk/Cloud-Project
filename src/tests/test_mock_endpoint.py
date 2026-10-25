from flask import Flask, jsonify, request

app = Flask(__name__)

# The gateway sends payloads to http://127.0.0.1:5000/execute
@app.route('/execute', methods=['POST'])
def execute_payload():
    print("\n[Mock Endpoint] Received a verified payload from the gateway!")
    
    # You can inspect the raw bytes if needed
    # raw_data = request.get_data()
    
    # Return the exact JSON structure that gateway_app.py expects on line 105
    return jsonify({
        "executed": True,
        "returncode": 0,
        "stdout": "Mock endpoint successfully received and processed the payload."
    }), 200

if __name__ == "__main__":
    print("Starting Mock Endpoint Server on port 5000...")
    app.run(port=5000)