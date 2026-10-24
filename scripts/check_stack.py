"""Start, exercise, and stop the complete local pipeline: python -m scripts.check_stack.

Requires free ports 5000/8000/8080/9000, Docker, the built image, and Nginx.
Never stops pre-existing services. Reports and service logs go under artifacts/.
"""
import argparse
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid

from dotenv import load_dotenv
import requests

from scripts.monitor_nginx import poll
from src.ingress import CLIENT_TIMEOUT, PROXY_READ_SECONDS

ROOT = Path(__file__).resolve().parents[1]


class StackCheck:
    def __init__(self, report):
        self.report = report
        report.mkdir(parents=True, exist_ok=True)
        self.events = []
        self.children = []
        self.handles = []
        self.nginx_started = False
        self.nginx = shutil.which("nginx") or "nginx"
        self.env = {**os.environ, "PYTHONUNBUFFERED": "1", "DASHBOARD_ENABLED": "0",
                    "GATEWAY_HOST": "127.0.0.1", "GATEWAY_PORT": "8000",
                    "GATEWAY_THREADS": "8", "GATEWAY_URL": "http://127.0.0.1:8080/submit",
                    "ENDPOINT_URL": "http://127.0.0.1:5000/execute",
                    "NOTIFY_URL": "http://127.0.0.1:9000/notify"}
        self.access = ROOT / "nginx/logs/access.log"
        self.log_offset = self.access.stat().st_size if self.access.exists() else 0
        self.error_log = ROOT / "nginx/logs/error.log"
        self.error_offset = self.error_log.stat().st_size if self.error_log.exists() else 0

    def event(self, name, detail="", status="passed"):
        self.events.append({"name": name, "detail": detail, "status": status})
        (self.report / "progress.json").write_text(json.dumps(self.events, indent=2))
        print(f"[{status.upper()}] {name}" + (f": {detail}" if detail else ""), flush=True)

    def command(self, args, timeout=180, env=None):
        result = subprocess.run(args, cwd=ROOT, env=env or self.env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        if result.returncode:
            (self.report / "failed-command.log").write_text(result.stdout)
            raise RuntimeError(f"{args[0]} failed ({result.returncode}): {result.stdout[-5000:]}")
        return result.stdout

    def nginx_command(self, *args):
        return self.command([self.nginx, "-p", str(ROOT / "nginx") + "/", "-c", "nginx.conf", *args])

    def start(self, module, port, name):
        log = (self.report / f"{name}.log").open("w")
        self.handles.append(log)
        process = subprocess.Popen([sys.executable, "-u", "-m", module], cwd=ROOT,
                                   env=self.env, stdout=log, stderr=subprocess.STDOUT)
        self.children.append(process)
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError(f"{name} stopped during startup; see {name}.log")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.2):
                    self.event(f"Start {name}", f"port {port}")
                    return process
            except OSError:
                time.sleep(.1)
        raise RuntimeError(f"{name} did not start within 10 seconds")

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def entries(self):
        if not self.access.exists():
            return []
        with self.access.open("rb") as stream:
            stream.seek(self.log_offset)
            return [json.loads(line) for line in stream if line.strip()]

    def cli(self, filename, flags, expected, reason=None):
        output = self.command([sys.executable, "-m", "src.sender.send", f"src/examples/{filename}", *flags])
        statuses = [int(line.rsplit(" ", 1)[-1]) for line in output.splitlines() if line.startswith("--- attempt")]
        if statuses != expected or (reason and reason not in output):
            raise AssertionError(output)
        with (self.report / "sender.log").open("a") as log:
            log.write(output + "\n")
        self.event(f"Sender: {filename} {' '.join(flags)}".strip(), f"HTTP {statuses}")

    def check_proxy_timeout(self):
        """A deliberately silent upstream tests the real configured timeout."""
        release = threading.Event()
        calls = []

        class SilentGateway(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                calls.append(1)
                release.wait(PROXY_READ_SECONDS + 5)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 8000), SilentGateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.event("Start controlled timeout probe", f"silent test upstream; waiting {PROXY_READ_SECONDS}s")
        try:
            started = time.monotonic()
            response = requests.post("http://127.0.0.1:8080/submit", json={}, timeout=CLIENT_TIMEOUT)
            elapsed = time.monotonic() - started
            assert response.status_code == 504 and response.json().get("stage") == "ingress"
            assert PROXY_READ_SECONDS - 1 <= elapsed < CLIENT_TIMEOUT[1]
            assert len(calls) == 1, "Proxy retried a POST"
            self.event("Proxy timeout and no retry", f"JSON 504 after {elapsed:.1f}s; one upstream attempt")
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def run(self, include_unit=True):
        for port in (5000, 8000, 8080, 9000):
            with socket.socket() as sock:
                try:
                    sock.bind(("127.0.0.1", port))
                except OSError as error:
                    raise RuntimeError(f"Port {port} is occupied. Stop that service first; see readme.md.") from error
        self.command(["docker", "info", "--format", "{{.OSType}}"])
        image = os.environ.get("SANDBOX_IMAGE", "cloudproject-sandbox:dev")
        user = self.command(["docker", "image", "inspect", image, "--format", "{{.Config.User}}"]).strip()
        if user != "65534:65534":
            raise RuntimeError("Sandbox image must use UID/GID 65534:65534; rebuild it per readme.md")
        self.nginx_command("-t")
        self.event("Prerequisites", "ports free; Docker image ready; Nginx configuration valid")
        if include_unit:
            output = self.command([sys.executable, "-m", "pytest", "src/tests", "-q", "-p", "no:cacheprovider"],
                                  env={**self.env, "DASHBOARD_ENABLED": "1"})
            (self.report / "pytest.log").write_text(output)
            self.event("Project tests", output.strip().splitlines()[-1])
        self.start("src.sender.central_server", 9000, "central")
        endpoint = self.start("src.endpoint.endpoint_app", 5000, "endpoint")
        gateway = self.start("src.gateway.serve", 8000, "gateway")
        self.nginx_command()
        self.nginx_started = True
        response = requests.get("http://127.0.0.1:8080/", timeout=3)
        assert response.status_code == 404 and response.headers.get("X-Request-ID"), "Wrong Nginx configuration on port 8080"
        self.event("Start project Nginx", "port 8080; request IDs present")
        # Live stdout keeps the user informed as each ingress check completes.
        result = subprocess.run([sys.executable, "-u", "-m", "scripts.check_nginx", "--benchmark",
                                 "--report", str(self.report / "ingress.json")], cwd=ROOT, env=self.env, timeout=180)
        if result.returncode:
            raise RuntimeError("Ingress checks failed; see ingress.json and service logs")
        self.event("Nginx protections and traffic measurements", "all live checks passed")
        self.cli("good.py", [], [200], "config update applied")
        self.cli("good.py", ["--tamper"], [401], "verify")
        self.cli("good.py", ["--replay"], [200, 401], "Replay Attack Detected")
        self.cli("infinite_loop.py", [], [422], "timeout")
        self.cli("memory_hog.py", [], [422], "oom_killed")
        self.stop(endpoint)
        self.cli("good.py", [], [502], "forward")
        self.event("Endpoint downtime", "gateway fails closed and reports forwarding failure")
        self.stop(gateway)
        response = requests.post("http://127.0.0.1:8080/submit", json={}, timeout=CLIENT_TIMEOUT)
        assert response.status_code == 502 and response.json().get("stage") == "ingress"
        self.cli("good.py", [], [502], "gateway unavailable")
        self.event("Gateway downtime", "Nginx JSON 502; sender reports the error cleanly")
        self.check_proxy_timeout()
        entries = self.entries()
        statuses = Counter(e["status"] for e in entries)
        for status in (413, 429):
            blocked = [e for e in entries if e["status"] == status]
            assert blocked and all(e["upstream_status"] in ("", "-") for e in blocked)
        assert any(e.get("limit_conn") == "REJECTED" for e in entries), "Concurrency rejection was not observed"
        assert any(e.get("limit_req") == "REJECTED" for e in entries), "Rate rejection was not observed"
        assert any(e["status"] == 408 and e["upstream_status"] in ("", "-") for e in entries)
        with self.error_log.open("rb") as stream:
            stream.seek(self.error_offset)
            assert b'limiting connections by zone "total_connections"' in stream.read(), "Server-wide limit was not exercised"
        self.event("Proxy isolation evidence", "size, rate, concurrency and stalled-body rejections have no upstream")
        # Start from this run's offset, avoiding historical alerts during verification.
        stat = self.access.stat()
        state = self.report / "monitor-state.json"
        state.write_text(json.dumps({"identity": [stat.st_dev, stat.st_ino], "offset": self.log_offset}))
        report = poll(self.access, state, self.env["NOTIFY_URL"])
        assert report and all(code in report["counts"] for code in ("413", "429", "502", "504", "408"))
        assert poll(self.access, state, self.env["NOTIFY_URL"]) is None
        self.event("Aggregated failure notification", json.dumps(report["counts"]))
        central = (self.report / "central.log").read_text()
        for text in ("'verify'", "'capacity'", "'dry_run'", "'forward'", "'ingress'"):
            assert text in central, f"Missing central notification for {text}"
        self.event("Central-server receipt", "all pipeline failure stages and ingress summary received")
        (self.report / "access.json").write_text(json.dumps(entries, indent=2))
        (self.report / "summary.json").write_text(json.dumps({"status_counts": dict(statuses),
                   "ingress_notification": report, "metrics": json.loads((self.report / "ingress.json").read_text())["metrics"]}, indent=2))

    def cleanup(self):
        errors = []
        if self.nginx_started:
            try:
                self.nginx_command("-s", "quit")
            except Exception as error:
                errors.append(str(error))
        for child in reversed(self.children):
            try:
                self.stop(child)
            except Exception as error:
                errors.append(str(error))
        for handle in self.handles:
            handle.close()
        try:
            containers = self.command(["docker", "ps", "-a", "--filter", "label=cloudproject.sandbox=1",
                                       "--format", "{{.Names}} {{.Status}}"])
            if containers.strip():
                errors.append("Sandbox containers remain: " + containers.strip())
        except Exception as error:
            errors.append(str(error))
        self.event("Shutdown and container cleanup", "; ".join(errors) or "test services stopped; no sandbox containers remain",
                   "failed" if errors else "passed")
        return not errors


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=ROOT / "artifacts" / ("nginx-" + uuid.uuid4().hex[:8]))
    parser.add_argument("--skip-unit", action="store_true", help="use only after unit tests have passed for this exact change")
    args = parser.parse_args()
    checker = StackCheck(args.report_dir.resolve())
    ok = False
    try:
        checker.run(not args.skip_unit)
        ok = True
    except (Exception, KeyboardInterrupt) as error:
        checker.event("Test run", str(error), "failed")
    finally:
        ok = checker.cleanup() and ok
        print(f"Report: {checker.report}", flush=True)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
