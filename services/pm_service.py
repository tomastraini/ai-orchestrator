from __future__ import annotations

import warnings

warnings.warn(
    "services.pm_service is deprecated; use services.pm.pm_service instead.",
    DeprecationWarning,
    stacklevel=2,
)

from services.pm.pm_service import PMServiceError, create_plan