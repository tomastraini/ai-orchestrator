from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


RunOnceResult = Tuple[List[str], Optional[str], Dict[str, Any]]
ExecuteDevTasksResult = Tuple[
    List[str],
    List[str],
    List[str],
    List[Dict[str, Any]],
    Optional[Dict[str, Any]],
    List[Dict[str, Any]],
]
RecoveryRunResult = Tuple[List[str], Optional[str], Dict[str, Any]]

DevTechnicalPlan = Dict[str, Any]

