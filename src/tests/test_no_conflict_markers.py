from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED = ("src", "scripts", "docker", "infra", "nginx/nginx.conf", "requirements.txt")

def test_no_merge_conflict_markers_are_committed():
    files = []
    for entry in SCANNED:
        path = ROOT / entry
        files += [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file() and p.suffix != ".pyc"]
    bad = []
    for path in files:
        if path == Path(__file__).resolve():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if line.startswith(("<<<<<<< ", ">>>>>>> ")):
                bad.append(f"{path.relative_to(ROOT)}:{number}")
    assert not bad, "merge conflict markers found: " + ", ".join(bad)