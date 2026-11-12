"""Recover and process durable EKS dry-run submissions."""

import logging
import threading
import time
import uuid

import requests


LOGGER = logging.getLogger(__name__)


class CloudWorker:
    def __init__(self, store, activity_store, runner_factory, notify_failure,
                 endpoint_url, max_workers=2):
        self.store = store
        self.activity_store = activity_store
        self.runner_factory = runner_factory
        self.notify_failure = notify_failure
        self.endpoint_url = endpoint_url
        self.max_workers = max_workers
        self._lock = threading.Lock()
        self._threads = []

    def start(self):
        with self._lock:
            for row in self.store.mark_stale_forwarding():
                try:
                    self._record_final(row, {"delivered": False, "stage": "forward",
                        "reason": "forwarding outcome is uncertain after gateway restart",
                        "execution_location": "eks"}, 503)
                except Exception:
                    LOGGER.exception("Could not update activity for stale submission %s", row["id"])
            self._threads = [thread for thread in self._threads if thread.is_alive()]
            for _ in range(self.max_workers - len(self._threads)):
                thread = threading.Thread(target=self._loop, daemon=True, name="eks-submission-worker")
                thread.start()
                self._threads.append(thread)

    def _loop(self):
        worker_id = uuid.uuid4().hex
        while True:
            try:
                row = self.store.claim(worker_id)
                if row is None:
                    time.sleep(0.5)
                    continue
                self._process(row, worker_id)
            except Exception:
                LOGGER.exception("EKS submission worker failed")
                time.sleep(2)

    def _heartbeat(self, submission_id, worker_id, stop):
        while not stop.wait(10):
            try:
                if not self.store.renew(submission_id, worker_id):
                    break
            except Exception:
                LOGGER.exception("Could not renew EKS submission lease")
                break

    def _record_final(self, row, response, status_code):
        event = {"id": row["id"], "name": row["name"], "scenario": row["scenario"],
                 "created_at": row["created_at"], "status_code": status_code,
                 "duration_ms": round((time.time() - row["created_at"]) * 1000, 2),
                 "response": response}
        self.activity_store.record(event)

    def _finish(self, row, worker_id, response, status_code, ambiguous=False):
        if self.store.finish(row["id"], worker_id, response, status_code, ambiguous=ambiguous):
            try:
                self._record_final(row, response, status_code)
            except Exception:
                LOGGER.exception("Could not update activity for EKS submission %s", row["id"])

    def _process(self, row, worker_id):
        stop = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat, args=(row["id"], worker_id, stop), daemon=True)
        heartbeat.start()
        timings = {"verify_ms": row["verify_ms"]}
        timings["queue_ms"] = round((time.time() - row["created_at"]) * 1000, 2)
        started = time.perf_counter()
        try:
            try:
                result = self.runner_factory().run(row["payload"], row["id"], row["language"])
            except Exception:
                LOGGER.exception("EKS dry run failed for submission %s", row["id"])
                result = {"status": "infrastructure_error", "detail": "EKS sandbox unavailable"}
            timings["dry_run_ms"] = round((time.perf_counter() - started) * 1000, 2)
            for key in ("cloud_ms", "pod_startup_ms", "execution_ms", "monitor_ms"):
                if key in result:
                    timings[key] = result[key]

            if result["status"] != "OK":
                reason = f'{result["status"]}: {result.get("detail", "")}'.strip(": ")
                response = {"delivered": False, "stage": "dry_run", "reason": reason,
                            "execution_location": "eks", "job_name": result.get("job_name"),
                            "timings_ms": timings}
                if result.get("behaviour"):
                    response["behaviour"] = result["behaviour"]   # Falco findings, shown by the API and dashboard
                status_code = 503 if result["status"] == "infrastructure_error" else 422
                self.notify_failure("dry_run", reason, row["nonce"])
                self._finish(row, worker_id, response, status_code)
                return

            if not self.store.begin_forward(row["id"], worker_id):
                LOGGER.error("Lost EKS submission lease before forwarding %s", row["id"])
                return

            started = time.perf_counter()
            try:
                response = requests.post(
                    self.endpoint_url, data=row["payload"],
                    headers={"Content-Type": "application/octet-stream",
                             "X-Payload-Language": row["language"]}, timeout=15,
                )
                response.raise_for_status()
                endpoint_response = response.json()
            except (requests.RequestException, ValueError):
                timings["forward_ms"] = round((time.perf_counter() - started) * 1000, 2)
                reason = "endpoint forwarding outcome is uncertain; no automatic retry"
                self.notify_failure("forward", reason, row["nonce"])
                self._finish(row, worker_id, {"delivered": False, "stage": "forward",
                    "reason": reason, "execution_location": "eks", "job_name": result.get("job_name"),
                    "timings_ms": timings}, 503, ambiguous=True)
                return

            timings["forward_ms"] = round((time.perf_counter() - started) * 1000, 2)
            if (not isinstance(endpoint_response, dict) or not endpoint_response.get("executed")
                    or endpoint_response.get("returncode") != 0):
                reason = "endpoint did not execute the script successfully"
                self.notify_failure("forward", reason, row["nonce"])
                self._finish(row, worker_id, {"delivered": False, "stage": "forward",
                    "reason": reason, "execution_location": "eks", "job_name": result.get("job_name"),
                    "timings_ms": timings}, 502)
                return

            for key in ("stdout", "stderr"):
                value = endpoint_response.get(key)
                if isinstance(value, str) and len(value) > 16000:
                    endpoint_response[key] = value[:16000] + "\n[output truncated]"

            delivered = {"delivered": True, "endpoint_response": endpoint_response,
                         "execution_location": "eks", "job_name": result.get("job_name"),
                         "timings_ms": timings}
            if result.get("behaviour"):
                delivered["behaviour"] = result["behaviour"]
            self._finish(row, worker_id, delivered, 200)
        finally:
            stop.set()
            heartbeat.join(timeout=1)
