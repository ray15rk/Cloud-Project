"""Local pipeline time budgets (seconds); keep nginx.conf in sync via tests.

These are inactivity/connect timeouts, not a guarantee of distributed cancellation.
Never automatically retry an uncertain script submission.
"""
GATEWAY_URL = "http://127.0.0.1:8080/submit"
PROXY_READ_SECONDS = 45
CLIENT_TIMEOUT = (3, 60)
ENDPOINT_TIMEOUT = (2, 15)
NOTIFY_TIMEOUT = (1, 2)
