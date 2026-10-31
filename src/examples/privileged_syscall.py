# Calls unshare(CLONE_NEWUSER). It fails in the sandbox; the call itself is what a monitor reports.
# Note: a syscall the pod's seccomp profile blocks outright may not reach Falco's probe at all.
import ctypes

libc = ctypes.CDLL(None, use_errno=True)
libc.unshare(0x10000000)
print("done")
