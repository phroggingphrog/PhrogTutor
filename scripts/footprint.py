#!/usr/bin/env python3
"""Measure how heavy the repo is: source size, installed dependencies, startup time, idle memory.

Run before and after a removal so "lighter" is a number, e.g.:

    python scripts/footprint.py > reports/footprint-<label>.json

Lines are counted without tests; ``node_modules`` sizes are on disk; the backend is started on a spare
port and read once it answers, then again after a few idle seconds.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("FOOTPRINT_PORT", "8021"))


def lines(*parts: str, suffixes: tuple[str, ...]) -> int:
    total = 0
    for part in parts:
        base = ROOT / part
        for path in base.rglob("*"):
            if path.suffix in suffixes and not {"node_modules", "__pycache__", ".next", "dist"} & set(path.parts):
                try:
                    total += sum(1 for _ in path.open("rb"))
                except OSError:
                    pass
    return total


def disk_mb(path: str) -> float | None:
    target = ROOT / path
    if not target.exists():
        return None
    out = subprocess.run(["du", "-sk", str(target)], capture_output=True, text=True).stdout.split()
    return round(int(out[0]) / 1024, 1) if out else None


def rss_mb(pid: int) -> float:
    total = 0
    kids = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
    for p in [str(pid), *kids]:
        try:
            for line in Path(f"/proc/{p}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
        except OSError:
            pass
    return round(total / 1024, 1)


def backend() -> dict[str, float]:
    start = time.time()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "deeptutor.api.main:app", "--port", str(PORT)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/notes", timeout=1).read()
                break
            except Exception:
                time.sleep(0.5)
        ready = round(time.time() - start, 1)
        first = rss_mb(proc.pid)
        time.sleep(5)
        return {"seconds_to_ready": ready, "rss_mb_at_ready": first, "rss_mb_after_5s_idle": rss_mb(proc.pid)}
    finally:
        proc.terminate()
        proc.wait(timeout=20)


def main() -> None:
    report = {
        "python_lines": lines("deeptutor", "deeptutor_cli", suffixes=(".py",)),
        "web_lines": lines("web/app", "web/components", "web/features", "web/hooks", "web/lib", "web/shared", suffixes=(".ts", ".tsx")),
        "pi_engine_lines": lines("pi_engine", suffixes=(".ts",)),
        "pi_engine_node_modules_mb": disk_mb("pi_engine/node_modules"),
        "web_node_modules_mb": disk_mb("web/node_modules"),
        "backend": backend() if "--no-run" not in sys.argv else None,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
