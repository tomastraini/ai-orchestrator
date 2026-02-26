from __future__ import annotations

import warnings

warnings.warn(
    "services.dev_executor is deprecated; use services.dev.dev_executor instead.",
    DeprecationWarning,
    stacklevel=2,
)

from services.dev.dev_executor import (
    DevExecutorError,
    classify_failure,
    execute_dev_tasks,
    execute_single_recovery_command,
    rewrite_command_deterministic,
)
