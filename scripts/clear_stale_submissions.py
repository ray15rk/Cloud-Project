#!/usr/bin/env python3
"""Utility to clear stale EKS submissions or completely reset local project databases."""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / ".data"
SUBMISSIONS_DB = DATA_DIR / "cloud_submissions.sqlite3"
ACTIVITY_DB = DATA_DIR / "activity.sqlite3"
NGINX_MONITOR = DATA_DIR / "nginx-monitor.json"


def reset_stale_submissions():
    """Reset any submissions stuck in queued/running/forwarding states."""
    if not SUBMISSIONS_DB.exists():
        print("No cloud submissions database found (.data/cloud_submissions.sqlite3).")
        return 0

    conn = sqlite3.connect(SUBMISSIONS_DB)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE cloud_submissions
        SET state = 'timed_out',
            lease_until = NULL,
            response_json = '{"delivered": false, "reason": "aborted mid-flight / manually cleared", "status": "timed_out"}'
        WHERE state IN ('queued', 'running', 'forwarding')
    """)
    updated = cursor.rowcount
    conn.commit()

    # Query active count
    active = cursor.execute("""
        SELECT COUNT(*) FROM cloud_submissions
        WHERE state IN ('queued', 'running', 'forwarding')
    """).fetchone()[0]
    conn.close()

    print(f"Cleared {updated} stale in-flight submission(s).")
    print(f"Active in-flight slots: {active} / 3")
    return updated


def wipe_all_databases():
    """Completely wipe all activity and submissions databases for a fresh start."""
    wiped = []
    for db_path in (SUBMISSIONS_DB, ACTIVITY_DB):
        if db_path.exists():
            try:
                os.remove(db_path)
                wiped.append(db_path.name)
            except Exception as e:
                print(f"Warning: could not delete {db_path.name}: {e}")

    if NGINX_MONITOR.exists():
        try:
            os.remove(NGINX_MONITOR)
            wiped.append(NGINX_MONITOR.name)
        except Exception:
            pass

    if wiped:
        print(f"Successfully cleared: {', '.join(wiped)}")
        print("Local state is now 100% clean and fresh (0 entries).")
    else:
        print("All local databases are already empty / non-existent.")


def main():
    parser = argparse.ArgumentParser(
        description="Reset stale EKS submissions or wipe local databases for a fresh clean state."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Completely delete all activity and submissions databases for a brand new start.",
    )
    args = parser.parse_args()

    if args.all:
        wipe_all_databases()
    else:
        reset_stale_submissions()


if __name__ == "__main__":
    main()

