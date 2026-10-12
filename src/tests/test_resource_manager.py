# Tests the adminssion bookkeeping instead of real containers first

import concurrent.futures
from src.resource_manager.resource_manager import ResourceManager

def test_admit_within_budget_succeeds():
    rm = ResourceManager(total_memory_mb=256)
    assert rm.try_admit("128m") is True

def test_admit_over_budget_rejected():
    rm = ResourceManager(total_memory_mb=100)
    assert rm.try_admit("128m") is False

def test_release_frees_slot():
    rm = ResourceManager(total_memory_mb=128)
    assert rm.try_admit("128m") is True
    assert rm.try_admit("64m") is False # No more room

    rm.release("128m")
    assert rm.try_admit("64m") is True # Got room

def test_concurrent_admission():
    # Fire simultaneous admissions on memory limits, confirm exactly how many succedd as budget allows
    # Dont allow more to succeed even under thread contention
    rm = ResourceManager(total_memory_mb=128) # Allow 1x128m

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: rm.try_admit("128m"), range(20)))

    # Only one should pass
    assert results.count(True) == 1