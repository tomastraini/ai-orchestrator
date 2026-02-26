from __future__ import annotations

import warnings

warnings.warn(
    "services.dev_service is deprecated; use services.dev.dev_service instead.",
    DeprecationWarning,
    stacklevel=2,
)

from services.dev.dev_service import DevService
