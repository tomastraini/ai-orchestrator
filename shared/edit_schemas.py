from __future__ import annotations

from typing import Any, Dict, List, Literal, TypedDict


EditOperation = Literal["replace_symbol", "insert_after_symbol", "update_imports", "patch_region", "rename_path"]


class EditRequest(TypedDict, total=False):
    operation: EditOperation
    file_path: str
    original_content: str
    parameters: Dict[str, Any]


class EditValidationResult(TypedDict, total=False):
    passed: bool
    checks: List[str]
    warnings: List[str]
    errors: List[str]
    confidence: float
    expected_target_class: str
    selected_target_class: str


class EditResult(TypedDict, total=False):
    operation: EditOperation
    success: bool
    changed: bool
    modified_content: str
    source_path: str
    target_path: str
    metadata: Dict[str, Any]
    pre_check: EditValidationResult
    post_check: EditValidationResult
