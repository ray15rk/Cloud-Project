"""Run one mounted script with a deadline and bounded container logs."""
import os
import selectors
import signal
import subprocess
import sys
import time
SCRIPT_DEADLINE_SECONDS = 10
MAX_OUTPUT_BYTES = 64 * 1024

def main() -> int:
    if len(sys.argv) > 2:
        command = sys.argv[1:]
    elif len(sys.argv) == 2:
        script = sys.argv[1]
        if script.endswith(".sh"):
            command = ["bash", script]
        elif script.endswith(".ps1"):
            command = ["pwsh", "-NoProfile", "-Command", f"& '{script}'"]
        else:
            command = [sys.executable, "-I", "-B", "-u", script]
    else:
        command = [sys.executable, "-I", "-B", "-u", "/sandbox/script.py"]
    child = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    deadline = time.monotonic() + SCRIPT_DEADLINE_SECONDS
    remaining = MAX_OUTPUT_BYTES
    truncated = False

    with selectors.DefaultSelector() as selector:
        selector.register(child.stdout, selectors.EVENT_READ, sys.stdout.buffer)
        selector.register(child.stderr, selectors.EVENT_READ, sys.stderr.buffer)
        while selector.get_map() or child.poll() is None:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
                print("script exceeded 10-second execution deadline", file=sys.stderr)
                return 124
            
            if not selector.get_map():
                time.sleep(min(timeout, 0.05))
                continue

            for key, _ in selector.select(timeout):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue

                if len(chunk) > remaining:
                    truncated = True

                if remaining:
                    written = chunk[:remaining]
                    key.data.write(written)
                    key.data.flush()
                    remaining -= len(written)

    child.wait()
    if truncated:
        print("\n[script output truncated at 64 KiB]", file=sys.stderr)
    return child.returncode if child.returncode >= 0 else 128 - child.returncode
if __name__ == "__main__":
    raise SystemExit(main())