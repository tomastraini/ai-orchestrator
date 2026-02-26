from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--start-dir", default="tests")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    cmd = [sys.executable, "-m", "unittest", "discover", "-s", args.start_dir, "-p", args.pattern, "-q"]
    result = subprocess.run(cmd, cwd=str(repo_root), check=False)
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())

