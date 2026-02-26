from __future__ import annotations

import warnings

warnings.warn(
    "services.pm_context_store is deprecated; use services.pm.pm_context_store instead.",
    DeprecationWarning,
    stacklevel=2,
)

from services.pm.pm_context_store import PMContextStore
