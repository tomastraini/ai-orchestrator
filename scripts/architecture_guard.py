from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Tuple


FORBIDDEN_IMPORTS: List[Tuple[str, str]] = [
    ("orchestrator.py", "from services.dev"),
    ("orchestrator.py", "DevService"),
    ("services/pm/pm_service.py", "dev_handoff_store"),
]


def _line_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except Exception:
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hard-max-lines", type=int, default=500)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    failures: List[str] = []
    for path in repo_root.rglob("*.py"):
        if any(part in {".venv", "venv", "__pycache__"} for part in path.parts):
            continue
        lines = _line_count(path)
        if lines > args.hard_max_lines:
            rel = path.relative_to(repo_root).as_posix()
            message = f"file too large: {rel} ({lines} lines)"
            if args.strict:
                failures.append(message)
            else:
                print(f"[ARCH_GUARD][WARN] {message}")

    for rel, token in FORBIDDEN_IMPORTS:
        target = repo_root / rel
        if not target.exists():
            continue
        content = target.read_text(encoding="utf-8", errors="ignore")
        if token in content:
            failures.append(f"forbidden dependency still present: {rel}")

    if failures:
        for failure in failures:
            print(f"[ARCH_GUARD] {failure}")
        return 1
    print("[ARCH_GUARD] ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

