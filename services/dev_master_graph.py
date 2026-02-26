from __future__ import annotations

import warnings

warnings.warn(
    "services.dev_master_graph is deprecated; use services.dev.dev_master_graph instead.",
    DeprecationWarning,
    stacklevel=2,
)

from services.dev.dev_master_graph import DevMasterGraph
