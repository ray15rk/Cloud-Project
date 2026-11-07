"""Read pod logs as text, bypassing the Kubernetes client's JSON auto-decoding.

CoreV1Api.read_namespaced_pod_log runs json.loads() on the response body. When the whole body is exactly
one JSON document, such as a Falco log window holding a single alert, the client then returns the Python
repr of the parsed object: single quotes, True/False. That is no longer JSON. Logs with two or more lines
come back unchanged, so the bug only shows for some logs, which makes it intermittent: a clean dry run
(one start alert) was lost while a threat run (several alerts) worked.

Reading the raw response and decoding it ourselves returns the exact log text for every log.
"""


def read_pod_log_text(core, call, name, namespace, **kwargs) -> str:
    """The pod's log as text. `call` is the retrying wrapper (EKSRunner._call); kwargs go to the API call."""
    response = call(core.read_namespaced_pod_log, name, namespace, _preload_content=False, **kwargs)
    try:
        data = getattr(response, "data", response)
        if isinstance(data, (bytes, bytearray)):
            return bytes(data).decode("utf-8", errors="replace")
        return data if isinstance(data, str) else str(data)
    finally:
        release = getattr(response, "release_conn", None)
        if callable(release):
            release()
