Install docker desktop
pip install -r requirements.txt
Run test file (from the Cloud-Project folder):      python -m pytest src/tests -v
Watchdog tests only (no Docker needed):             python -m pytest src/tests -v -m "not docker"
Before the first run, download the sandbox image:   docker pull python:3.11-slim

Endpoint test:
python src/endpoint/endpoint_app.py

 @"
import requests
response = requests.post(
     'http://127.0.0.1:5000/execute',
     data=b'print("hello from the endpoint")',
     headers={'Content-Type': 'application/octet-stream'},
 )
print(response.json())
"@ | python

TODO NEXT: 
**The scripts testing assumes all is in python for now, need to change once all is done
Need to chain the watchdog and sandbox together and call endpoint process if everything passes