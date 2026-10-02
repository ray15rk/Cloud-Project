Install docker desktop
pip install -r requirements.txt
Run test file:  python -m pytest tests/ -v

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

TODO NEXT: Need to chain the watchdog and sandbox together and call endpoint process if everything passes