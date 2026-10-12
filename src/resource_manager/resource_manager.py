# Decide weather a payload's dry run can run locally before the dry run execution starts
# NOTE: EKS bursting not implemented yet, so when local host at capacity, it fails closed rather than running over budget

import threading
from dataclasses import dataclass, field

def _parse_memory_limit(memory_limit: str) -> int:
    # Convert docker memory string (128m, 1g) into megabytes, so resource manager can work on it
    # Mirrors same memory_limit string that goes into sandbox.py

    memory_limit = memory_limit.strip().lower()
    if memory_limit.endswith("g"):
        return int(memory_limit[:-1]) * 1024
    
    if memory_limit.endswith("m"):
        return int(memory_limit[:-1])

    raise ValueError(f"unsupported memory_limit format: {memory_limit!r}")

@dataclass
class ResourceManager:
    # Tracks memory currently commited to localdry runs using atomic counter
    # Every admission decision reads live lock protected total so there is no staleness window between check and commit under concurrent requests
    # Memory is tracked instead of CPU as memory exhaustio crashes the host via OOM killer, while CPU contention under cgrpup limit just throttles gracefully

    total_memory_mb: int = 512 # Local budget
    _committed_mb: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, init=False)

    def try_admit(self, memory_limit: str) -> bool:
        # Automatically check and commit in one step
        # If got room, reserve it and return True
        # If no room, commit nothing and rerturn False
        # Doing both under the same lock acqusition: Two threads calling this for the last slot should not both see that there is room
        needed_mb = _parse_memory_limit(memory_limit)
        with self._lock:
            if self._committed_mb + needed_mb > self.total_memory_mb:
                return False
            self._committed_mb += needed_mb
            return True

    def release(self, memory_limit: str) -> None:
        # Call once a local dry run's container is torn down to free the slot
        freed_mb = _parse_memory_limit(memory_limit)
        with self._lock:
            self._committed_mb = max(0, self._committed_mb - freed_mb)

# One instance shared across the whole process
# Every request's admission decidion needs to see the same live counter
resource_manager = ResourceManager()