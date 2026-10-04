# Cloud Computing Project

## SETUP
1. Install Python 3.11+ and Docker Desktop.
 
2. From the Cloud-Project folder, create and activate a virtual environment:
    python -m venv .venv
    .venv\Scripts\Activate.ps1          (Windows)
    source .venv/bin/activate           (macOS / Linux)
 
3. pip install -r requirements.txt
 
4. Create a .env where .env.example is, copy contents, and set a real secret:
    python -c "import secrets; print(secrets.token_hex(32))"
    Paste the result as SHARED_SECRET. .env is gitignored - never commit it.
 
5. Pre-pull the sandbox image, otherwise the first dry run is counted as a timeout:
    docker pull python:3.11-slim
 
All commands must be run from the Cloud-Project folder.

## TESTS
All tests (Docker Desktop must be running):   python -m pytest src/tests -v
Without Docker:                               python -m pytest src/tests -v -m "not docker"
 
 
After the run, confirm no sandbox containers leaked: docker ps --filter label=cloudproject.sandbox=1

### Endpoint test (debugging only)
Tests the endpoint on its own, bypassing the gateway. 
Useful for checking whether a failure in the full demo comes from the endpoint or from earlier in the pipeline.

**WARNING**: this skips the HMAC check and the sandbox. The endpoint runs whatever it is sent, so only send code you wrote yourself. 
This is not how payloads normally reach it.

Terminal 1:  python src/endpoint/endpoint_app.py

Terminal 2 (PowerShell):
  @"
import requests
response = requests.post(
     'http://127.0.0.1:5000/execute',
     data=b'print("hello from the endpoint")',
     headers={'Content-Type': 'application/octet-stream'},
 )
print(response.json())
"@ | python

## NOTES

TODO NEXT: 
**The scripts testing assumes all is in python for now, need to change once all is done.
- Resource manager. Also need to add a lock to NonceStore.