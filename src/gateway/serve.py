"""Run the gateway behind NGINX: ONE process, many threads.   python -m src.gateway.serve

Why one process: NonceStore (replay protection) and ResourceManager (memory budget) live in
process memory. A second worker would get its own copy of each, so the memory budget would be
multiplied and a replayed message could be accepted once per worker.

Why more threads than slots: the capacity gate only sees a request when a thread is free to run
it. If threads <= slots, overflow waits in the server's queue and try_admit() never returns
False, so cloud bursting would never trigger. (waitress defaults to 4 threads, which equals the
4 slots, so the default would silently hide overflow.)
"""
import os
import logging

MAX_BODY_BYTES = 256 * 1024  # same limit as gateway_app's MAX_CONTENT_LENGTH


def choose_threads(slots: int, requested: str | None) -> int:
    threads = int(requested) if requested else slots * 2
    if threads <= slots:
        raise SystemExit(
            f"GATEWAY_THREADS={threads} must be greater than the {slots} sandbox slots. "
            "With fewer threads, overflow requests queue inside the web server and the capacity "
            "check never sees them, so bursting would never trigger.")
    return threads


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # The NGINX-fronted process serves submissions and their status. Set BEFORE importing gateway_app so the
    # operator dashboard routes are never registered and cannot be exposed by a proxy mistake.
    os.environ.setdefault("DASHBOARD_ENABLED", "0")

    from waitress import serve
    from src.gateway.gateway_app import app, SHARED_SECRET, startup_warnings
    from src.resource_manager.resource_manager import resource_manager

    if not SHARED_SECRET:
        raise SystemExit("SHARED_SECRET is not set. Copy env.example to .env and fill it in.")

    for note in startup_warnings():
        print(f"[gateway] WARNING: {note}", flush=True)
    slots = resource_manager.snapshot()["slots"]
    threads = choose_threads(slots, os.environ.get("GATEWAY_THREADS"))
    host = os.environ.get("GATEWAY_HOST", "127.0.0.1")
    port = int(os.environ.get("GATEWAY_PORT", "8000"))
    print(f"Gateway on http://{host}:{port}  ({threads} threads, {slots} sandbox slots, dashboard off)", flush=True)
    # waitress enforces the body cap itself, so reaching port 8000 directly cannot bypass NGINX's limit
    serve(app, host=host, port=port, threads=threads, max_request_body_size=MAX_BODY_BYTES)


if __name__ == "__main__":
    main()
