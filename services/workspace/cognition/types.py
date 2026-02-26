from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class SymbolRecord(TypedDict, total=False):
    name: str
    kind: str
    line: int
    end_line: int


class ImportRecord(TypedDict, total=False):
    module: str
    members: List[str]
    is_external: bool


class RelationshipRecord(TypedDict, total=False):
    type: str
    target: str


class FileRecord(TypedDict, total=False):
    path: str
    kind: str
    language: str
    symbols: List[SymbolRecord]
    imports: List[ImportRecord]
    exports: List[Dict[str, Any]]
    relationships: List[RelationshipRecord]
    entrypoint_score: float
    confidence: float


class CognitionIndex(TypedDict, total=False):
    version: str
    generated_at: str
    project_root: str
    workspace_hash: str
    provider_capabilities: Dict[str, bool]
    file_count: int
    files: List[FileRecord]
    symbol_index: Dict[str, Any]
    graph: Dict[str, Any]
    dependency_graph: Dict[str, Any]
    entrypoints: List[Dict[str, Any]]
    entrypoint_aliases: Dict[str, List[str]]
    tests: List[str]
    test_mappings: List[Dict[str, str]]
    configs: List[str]
    toolchain: List[Dict[str, Any]]
    path_aliases: Dict[str, str]
    architecture_signals: List[Dict[str, Any]]
    resolution_hints: Dict[str, Any]
