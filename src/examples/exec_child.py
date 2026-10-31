# Starts another program. Exits 0. Falco reports the unexpected child process.
import subprocess

subprocess.run(["/bin/echo", "hello from a child process"])
