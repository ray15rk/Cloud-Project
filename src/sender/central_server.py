# Simulates the centralized device/server receiving failure notifications from the gateway
# Usage: python -m src.sender.central_server
 
from flask import Flask, request
 
app = Flask(__name__)
 
@app.route("/notify", methods=["POST"])
def notify():
    report = request.get_json(silent=True) or {}
    print(f"[central] payload dropped at '{report.get('stage')}': {report.get('reason')} "
          f"(nonce={report.get('nonce')}) -> admin should investigate or resend")
    return {"received": True}
 
if __name__ == "__main__":
    app.run(port=9000)
 