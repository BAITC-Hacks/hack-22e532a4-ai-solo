from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)


if __name__ == "__main__":
    run([sys.executable, "-m", "pytest"])
    run([sys.executable, "scripts/evaluate.py"])
    print("verify: OK")
