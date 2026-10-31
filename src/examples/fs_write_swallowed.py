# Tries to write to disk, catches the read-only filesystem error and exits 0.
try:
    with open("/sandbox/dropped.txt", "w") as handle:
        handle.write("x")
except OSError:
    pass
print("done")
