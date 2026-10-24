"""Run all three local services with one command: python -m src.dashboard."""
import threading
from werkzeug.serving import make_server

from src.gateway.gateway_app import app as gateway_app, SHARED_SECRET, cloud_worker, node_scaler
from src.endpoint.endpoint_app import app as endpoint_app
from src.sender.central_server import app as central_app


def main():
    if not SHARED_SECRET:
        raise SystemExit("Set SHARED_SECRET in .env before starting the dashboard.")
    servers = []
    try:
        for port, app in ((9000, central_app), (5000, endpoint_app), (8000, gateway_app)):
            servers.append(make_server("127.0.0.1", port, app, threaded=True))
    except (OSError, SystemExit):
        for server in servers:
            server.server_close()
        raise SystemExit("Could not start all services. Stop the old gateway, endpoint, and central terminals with Ctrl+C, then retry.")
    for server in servers[:-1]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    print("\nRelay dashboard: http://127.0.0.1:8000\nAll three local services are running. Press Ctrl+C to stop.\n", flush=True)
    if node_scaler:
        node_scaler.start()
        cloud_worker.start()
    try:
        servers[-1].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers[:-1]:
            server.shutdown()
        for server in servers:
            server.server_close()


if __name__ == "__main__":
    main()
