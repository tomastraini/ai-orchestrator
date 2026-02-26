from __future__ import annotations

from typing import Any, Dict, Protocol


class HandoffWriter(Protocol):
    def write_latest(self, handoff: Dict[str, Any]) -> None:
        ...

