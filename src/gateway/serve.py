"""Run the gateway behind NGINX: ONE process, many threads.   python -m src.gateway.serve

Why one process: NonceStore (replay protection) lives in process memory.
A second worker could accept a replayed message once per worker.
"""
import os
import logging

MAX_BODY_BYTES = 256 * 1024  # same limit as gateway_app's MAX_CONTENT_LENGTH


def choose_threads(requested: str | None) -> int:
    threads = int(requested) if requested else 8
    if threads < 1:
        raise SystemExit("GATEWAY_THREADS must be at least 1.")
    return threads


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # The NGINX-fronted process serves submissions and their status. Set BEFORE importing gateway_app so the
    # operator dashboard routes are never registered and cannot be exposed by a proxy mistake.
    os.environ.setdefault("DASHBOARD_ENABLED", "0")

    from waitress import serve
    from src.gateway.gateway_app import app, SHARED_SECRET, startup_warnings

    if not SHARED_SECRET:
        raise SystemExit("SHARED_SECRET is not set. Copy env.example to .env and fill it in.")

    for note in startup_warnings():
        print(f"[gateway] WARNING: {note}", flush=True)
    threads = choose_threads(os.environ.get("GATEWAY_THREADS"))
    host = os.environ.get("GATEWAY_HOST", "127.0.0.1")
    port = int(os.environ.get("GATEWAY_PORT", "8000"))
    print(f"Gateway on http://{host}:{port}  ({threads} threads, EKS dry runs, dashboard off)", flush=True)
    # waitress enforces the body cap itself, so reaching port 8000 directly cannot bypass NGINX's limit
    serve(app, host=host, port=port, threads=threads, max_request_body_size=MAX_BODY_BYTES)


if __name__ == "__main__":
    main()
