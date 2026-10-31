# Tries the network, catches the failure and exits 0. The old exit-code check calls this harmless;
# Falco reports the socket and connect attempt.
import socket

try:
    s = socket.socket()
    s.settimeout(1)
    s.connect(("93.184.216.34", 80))
except OSError:
    pass
print("done")
