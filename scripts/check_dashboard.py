"""Real browser + Docker checks. Run: python -m scripts.check_dashboard.

Uses temporary activity storage and ephemeral ports; existing local services are untouched.
Requires requirements-dev.txt and `python -m playwright install chromium`.
"""
import csv
import io
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright, expect
from flask import request, request_finished
from werkzeug.serving import WSGIRequestHandler, make_server

import src.gateway.gateway_app as gateway
from src.gateway.activity import ActivityStore
from src.endpoint.endpoint_app import app as endpoint
from src.sender.central_server import app as central


class QuietHandler(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


def main():
    artifacts = Path("artifacts")
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="relay-browser-check-") as temporary:
        gateway.activity_store = ActivityStore(Path(temporary) / "activity.sqlite3")
        servers = [make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietHandler)
                   for app in (endpoint, central, gateway.app)]
        gateway.ENDPOINT_URL = f"http://127.0.0.1:{servers[0].server_port}/execute"
        gateway.NOTIFY_URL = f"http://127.0.0.1:{servers[1].server_port}/notify"
        notifications = []
        def record_notification(sender, response, **extra):
            if request.path == "/notify" and response.status_code == 200:
                notifications.append(request.get_json())
        request_finished.connect(record_notification, sender=central, weak=False)
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1100}, reduced_motion="reduce", permissions=["clipboard-read", "clipboard-write"])
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                url = f"http://127.0.0.1:{servers[2].server_port}"
                page.goto(url)
                expect(page).to_have_title("Relay · Secure code delivery")
                expect(page.locator("#stat-total")).to_have_text("0")
                expect(page.locator("#empty-state")).to_be_visible()
                health = page.request.get(url + "/api/health").json()
                assert all(health[name]["status"] == "online" for name in ("gateway", "docker", "endpoint", "central")), health
                assert health["image"]["status"] == "ready", health
                print("PASS: page, empty state, real service health")

                # Exercise the original sender CLI as a separate process over real HTTP.
                for flags, expected in (([], [200]), (["--tamper"], [401]), (["--replay"], [200, 401])):
                    result = subprocess.run(
                        [sys.executable, "-m", "src.sender.send", "src/examples/good.py", *flags],
                        env={**os.environ, "GATEWAY_URL": url + "/submit", "SHARED_SECRET": gateway.SHARED_SECRET.decode("utf-8")},
                        capture_output=True, text=True, encoding="utf-8", timeout=30,
                    )
                    assert result.returncode == 0, result.stderr
                    import re
                    assert [int(code) for code in re.findall(r"HTTP (\d+)", result.stdout)] == expected, result.stdout
                assert all("payload" not in notice and "tag" not in notice for notice in notifications)
                print("PASS: original CLI delivery, tamper and replay over HTTP; central server received failure metadata")

                def run(expected_codes, *, mode="signed", example="good", code=None, name=None):
                    page.locator('.nav-item[data-view="workbench"]').click()
                    if example:
                        page.locator("#example-select").select_option(example)
                    if code is not None:
                        page.locator("#code-editor").fill(code)
                    if name:
                        page.locator("#filename").fill(name)
                    page.locator(f'[data-mode="{mode}"]').click()
                    with page.expect_response(lambda response: response.url.endswith("/api/run") and response.request.method == "POST", timeout=45000) as pending:
                        page.locator("#run-button").click()
                    response = pending.value
                    assert response.status == 200, response.text()
                    attempts = response.json()["attempts"]
                    assert [entry["status_code"] for entry in attempts] == expected_codes, attempts
                    expect(page.locator("#run-button")).to_be_enabled(timeout=10000)
                    return attempts

                run([200])
                expect(page.locator("#execution-output")).to_contain_text("config update applied")
                run([401], mode="tamper")
                run([200, 401], mode="replay")
                assert "timeout" in run([422], example="infinite_loop")[0]["response"]["reason"]
                assert "oom_killed" in run([422], example="memory_hog")[0]["response"]["reason"]
                print("PASS: delivery, tamper, replay, real deadline, real OOM isolation")

                page.locator("#file-input").set_input_files({"name": "config_update.py", "mimeType": "text/x-python", "buffer": b'print("uploaded script executed")'})
                expect(page.locator("#filename")).to_have_value("config_update.py")
                run([200], example=None)
                expect(page.locator("#execution-output")).to_contain_text("uploaded script executed")
                run([200], code='print(\'<img src=x onerror="window.injected=true">\')', name="=formula.py")
                assert page.locator("#execution-output img").count() == 0
                assert page.evaluate("window.injected === undefined")
                page.locator("#copy-output").click()
                assert "<img" in page.evaluate("navigator.clipboard.readText()")
                print("PASS: upload, custom editing, escaped output, clipboard")

                unicode_text = "Hello, caf\u00e9 \u4f60\u597d \U0001f680"
                run([200], code=f"print({unicode_text!r})", name="unicode.py")
                expect(page.locator("#execution-output")).to_contain_text(unicode_text)
                page.locator("#file-input").set_input_files({"name": "invalid.txt", "mimeType": "text/plain", "buffer": b"not python"})
                expect(page.locator("#toast")).to_contain_text("Choose a Python (.py) file")
                assert page.locator("#file-input").input_value() == ""
                page.locator("#file-input").set_input_files({"name": "oversized.py", "mimeType": "text/x-python", "buffer": b"#" * 65537})
                expect(page.locator("#toast")).to_contain_text("64 KB or smaller")
                page.locator("#code-editor").fill("")
                expect(page.locator("#run-button")).to_be_disabled()
                transfer = page.evaluate_handle("""() => {
                    const data = new DataTransfer();
                    data.items.add(new File(['print("drag and drop executed")'], 'dropped.py', {type: 'text/x-python'}));
                    return data;
                }""")
                page.locator("#drop-zone").dispatch_event("drop", {"dataTransfer": transfer})
                expect(page.locator("#filename")).to_have_value("dropped.py")
                run([200], example=None)
                expect(page.locator("#execution-output")).to_contain_text("drag and drop executed")
                print("PASS: UTF-8 execution, invalid/oversized file rejection, empty-editor guard, drag and drop")

                page.locator('.nav-item[data-view="system"]').click()
                expect(page.locator("#service-list")).to_contain_text("online", timeout=10000)
                with page.expect_response(lambda response: response.url.endswith("/api/health?refresh=1")):
                    page.locator("#refresh-health").click()
                with page.expect_response(lambda response: response.url.endswith("/api/capacity-test"), timeout=45000) as pending:
                    page.locator("#capacity-test").click()
                capacity = pending.value.json()
                assert capacity["submitted"] == 6, capacity
                assert sum(item["response"].get("stage") == "capacity" for item in capacity["attempts"]) == 2, capacity
                assert sum(item["response"]["delivered"] for item in capacity["attempts"]) == 4, capacity
                expect(page.locator("#run-button")).to_be_enabled(timeout=10000)
                assert gateway.resource_manager.snapshot()["committed_mb"] == 0
                print("PASS: concurrent capacity test, four local admissions, two safe rejections, memory release")

                original_run = gateway.dry_run
                def unavailable(*args, **kwargs):
                    raise FileNotFoundError("Docker unavailable for controlled failure check")
                gateway.dry_run = unavailable
                try:
                    assert run([503])[0]["response"]["stage"] == "dry_run"
                finally:
                    gateway.dry_run = original_run
                original_url = gateway.ENDPOINT_URL
                gateway.ENDPOINT_URL = "http://127.0.0.1:1/execute"
                try:
                    assert run([502])[0]["response"]["stage"] == "forward"
                finally:
                    gateway.ENDPOINT_URL = original_url
                print("PASS: sandbox unavailable and endpoint unavailable are visible failures")
                assert {"verify", "dry_run", "capacity", "forward"} <= {notice["stage"] for notice in notifications}

                page.locator('.nav-item[data-view="activity"]').click()
                page.locator("#activity-search").fill("config_update")
                expect(page.locator("#activity-body tr")).to_have_count(1)
                page.locator("#activity-search").fill("no-such-payload-xyz")
                expect(page.locator("#empty-state")).to_be_visible()
                page.locator("#activity-search").fill("")
                page.locator('[data-filter="blocked"]').click()
                assert page.locator("#activity-body tr").count() >= 5
                assert not page.locator("#activity-body").get_by_text("Delivered", exact=True).count()
                page.locator('[data-filter="all"]').click()
                with page.expect_download() as download:
                    page.locator("#export-button").click()
                csv_text = Path(download.value.path()).read_text(encoding="utf-8-sig")
                csv_rows = list(csv.reader(io.StringIO(csv_text)))
                assert any(row[1] == "'=formula.py" for row in csv_rows[1:]), csv_text
                page.locator(".row-detail").first.click()
                expect(page.locator("#detail-dialog")).to_be_visible()
                page.locator("#copy-detail").click()
                assert "status_code" in page.evaluate("navigator.clipboard.readText()")
                page.keyboard.press("Escape")
                page.locator("#notification-button").click()
                expect(page.locator("#notification-panel")).to_be_visible()
                assert page.locator(".notification-entry").count() > 0
                page.keyboard.press("Escape")
                expect(page.locator("#notification-panel")).to_be_hidden()
                expect(page.locator("#notification-button")).to_have_attribute("aria-expanded", "false")
                page.reload()
                expect(page.locator("#activity-body tr").first).to_be_visible()
                print("PASS: search, filters, empty results, safe CSV, detail dialog, notifications, history after reload")

                run([200])
                page.locator('.nav-item[data-view="overview"]').click()
                page.locator("#toast").evaluate("node => node.hidden = true")
                page.mouse.move(750, 50)
                page.screenshot(path=str(artifacts / "relay-desktop.png"), full_page=True)
                for width in (390, 768):
                    page.set_viewport_size({"width": width, "height": 844})
                    for view in ("overview", "workbench", "activity", "system"):
                        if width <= 760:
                            page.locator("#menu-button").click()
                        page.locator(f'.nav-item[data-view="{view}"]').click()
                        overflow = page.evaluate("""() => ({width: innerWidth, actual: document.documentElement.scrollWidth,
                            elements: [...document.querySelectorAll('body *')].filter(el => el.getBoundingClientRect().right > innerWidth + 1)
                            .map(el => ({tag: el.tagName, id: el.id, cls: el.className?.baseVal ?? el.className,
                                        right: Math.round(el.getBoundingClientRect().right)})).slice(0, 20)})""")
                        if overflow["actual"] > width:
                            page.screenshot(path=str(artifacts / "layout-overflow.png"), full_page=True)
                        assert overflow["actual"] <= width, (width, view, overflow)
                    if width == 390:
                        page.locator("#menu-button").click()
                        page.locator('.nav-item[data-view="overview"]').click()
                        page.screenshot(path=str(artifacts / "relay-mobile.png"), full_page=True)
                assert not errors, errors
                print("PASS: desktop/mobile navigation and layouts; no uncaught browser errors")
                context.close()
                browser.close()
        finally:
            request_finished.disconnect(record_notification, sender=central)
            for server in servers:
                server.shutdown()
                server.server_close()
    print("All dashboard browser checks passed. Screenshots: artifacts/relay-desktop.png and artifacts/relay-mobile.png")


if __name__ == "__main__":
    main()
