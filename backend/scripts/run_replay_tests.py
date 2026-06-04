"""Run replay integration tests with pytest.

Usage:
    python scripts/run_replay_tests.py
    python scripts/run_replay_tests.py -k websocket
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    backend_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        "-m",
        "pytest",
        str(backend_root / "tests"),
        *argv[1:],
    ]
    return subprocess.call(command, cwd=str(backend_root))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
