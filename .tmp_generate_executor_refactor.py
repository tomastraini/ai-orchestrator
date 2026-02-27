from __future__ import annotations

import ast
from pathlib import Path


def main() -> None:
    root = Path("services/dev")
    src_path = root / "dev_executor.py"
    src = src_path.read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)
    funcs = {n.name: (n.lineno, n.end_lineno) for n in tree.body if isinstance(n, ast.FunctionDef)}

    def get_function(name: str) -> str:
        start, end = funcs[name]
        return "\n".join(lines[start - 1 : end])

    (root / "executor_scope.py").write_text(
        """from __future__ import annotations

import os

from shared.pathing import _collapse_nested_projects_segments, canonicalize_scope_path


class DevExecutorError(RuntimeError):
    pass


"""
        + get_function("_normalize_scope_path")
        + "\n\n"
        + get_function("_assert_within_scope")
        + "\n\n"
        + get_function("_resolve_cwd")
        + "\n",
        encoding="utf-8",
    )

    (root / "executor_policy.py").write_text(
        """from __future__ import annotations

from typing import List, Optional


"""
        + get_function("_is_blocked_command")
        + "\n\n"
        + get_function("_violates_constraints")
        + "\n\n"
        + get_function("_is_likely_long_running_command")
        + "\n",
        encoding="utf-8",
    )

    (root / "executor_telemetry.py").write_text(
        """from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional


PROMPT_REGEX = re.compile(
    r"(ok to proceed\\??|proceed\\??|\\[y/n\\]|\\(y/n\\)|\\(y/N\\)|\\(Y/n\\)|confirm\\??)",
    re.IGNORECASE,
)
SERVICE_READY_REGEX = re.compile(
    r"(ready|localhost:|listening on|started|running at|server)",
    re.IGNORECASE,
)
SECRET_PATTERNS = [
    re.compile(r"(api[_-]?key\\s*[:=]\\s*)([^\\s\\\"']+)", re.IGNORECASE),
    re.compile(r"(token\\s*[:=]\\s*)([^\\s\\\"']+)", re.IGNORECASE),
    re.compile(r"(password\\s*[:=]\\s*)([^\\s\\\"']+)", re.IGNORECASE),
]


"""
        + get_function("_emit")
        + "\n\n"
        + get_function("_sanitize_log_value")
        + "\n\n"
        + get_function("_emit_event")
        + "\n",
        encoding="utf-8",
    )

    (root / "executor_rewrite.py").write_text(
        """from __future__ import annotations

import os
import re

from services.dev.command_policy import normalize_command_for_stack, normalize_non_interactive
from services.dev.executor_scope import _normalize_scope_path


"""
        + get_function("classify_failure")
        + "\n\n"
        + get_function("rewrite_command_deterministic")
        + "\n",
        encoding="utf-8",
    )

    (root / "executor_runtime.py").write_text(
        """from __future__ import annotations

import subprocess
import threading
import time
from queue import Empty, Queue
from typing import Callable, Dict, List, Literal, Optional

from services.dev.executor_telemetry import PROMPT_REGEX, SERVICE_READY_REGEX, _emit
from services.dev.types.executor_types import RecoveryRunResult, RunOnceResult


"""
        + get_function("_run_once")
        + "\n\n"
        + get_function("execute_single_recovery_command")
        + "\n",
        encoding="utf-8",
    )

    (root / "executor_task_engine.py").write_text(
        """from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.dev.command_policy import assess_risk, detect_stack_from_command
from services.dev.executor_policy import _is_blocked_command, _is_likely_long_running_command, _violates_constraints
from services.dev.executor_rewrite import classify_failure, rewrite_command_deterministic
from services.dev.executor_runtime import _run_once
from services.dev.executor_scope import DevExecutorError, _resolve_cwd
from services.dev.executor_telemetry import _emit, _emit_event, _sanitize_log_value
from services.dev.types.executor_types import ExecuteDevTasksResult
from shared.dev_schemas import DevTask


"""
        + get_function("execute_dev_tasks")
        + "\n",
        encoding="utf-8",
    )

    facade = """from __future__ import annotations

from services.dev.executor_policy import _is_blocked_command, _is_likely_long_running_command, _violates_constraints
from services.dev.executor_rewrite import classify_failure, rewrite_command_deterministic
from services.dev.executor_scope import DevExecutorError, _assert_within_scope, _normalize_scope_path, _resolve_cwd
from services.dev.executor_runtime import _run_once, execute_single_recovery_command
from services.dev.executor_task_engine import execute_dev_tasks
from services.dev.executor_telemetry import (
    PROMPT_REGEX,
    SECRET_PATTERNS,
    SERVICE_READY_REGEX,
    _emit,
    _emit_event,
    _sanitize_log_value,
)

__all__ = [
    "DevExecutorError",
    "PROMPT_REGEX",
    "SERVICE_READY_REGEX",
    "SECRET_PATTERNS",
    "_normalize_scope_path",
    "_assert_within_scope",
    "_resolve_cwd",
    "_is_blocked_command",
    "_violates_constraints",
    "_emit",
    "_sanitize_log_value",
    "_emit_event",
    "_is_likely_long_running_command",
    "classify_failure",
    "rewrite_command_deterministic",
    "_run_once",
    "execute_dev_tasks",
    "execute_single_recovery_command",
]
"""
    src_path.write_text(facade, encoding="utf-8")


if __name__ == "__main__":
    main()
