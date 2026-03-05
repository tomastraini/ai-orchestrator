from __future__ import annotations

from enum import Enum
from typing import Iterable, List


class RoleName(str, Enum):
    PM = "pm"
    PLANNER = "planner"
    BUILDER = "builder"
    VALIDATOR = "validator"
    FINALIZER = "finalizer"
    TEST_FIXER = "test_fixer"
    DEPENDENCY_FIXER = "dependency_fixer"
    DOCS_WRITER = "docs_writer"
    SECURITY_REVIEWER = "security_reviewer"


class StageName(str, Enum):
    PLAN_INGESTED = "plan_ingested"
    DISPATCH_STARTED = "dispatch_started"
    PLANNER_COMPLETED = "planner_completed"
    BUILDER_COMPLETED = "builder_completed"
    VALIDATOR_COMPLETED = "validator_completed"
    FINALIZER_COMPLETED = "finalizer_completed"
    EVIDENCE_PUBLISHED = "evidence_published"
    RUN_COMPLETED = "run_completed"


STAGE_SEQUENCE: List[str] = [
    StageName.PLAN_INGESTED.value,
    StageName.DISPATCH_STARTED.value,
    StageName.PLANNER_COMPLETED.value,
    StageName.BUILDER_COMPLETED.value,
    StageName.VALIDATOR_COMPLETED.value,
    StageName.FINALIZER_COMPLETED.value,
    StageName.EVIDENCE_PUBLISHED.value,
    StageName.RUN_COMPLETED.value,
]


def validate_stage_sequence(emitted_stages: Iterable[str]) -> List[str]:
    errors: List[str] = []
    emitted = list(emitted_stages)
    position = 0
    for stage in emitted:
        while position < len(STAGE_SEQUENCE) and STAGE_SEQUENCE[position] != stage:
            position += 1
        if position >= len(STAGE_SEQUENCE):
            errors.append(f"Unknown or out-of-order stage emitted: {stage}")
            continue
    for required in STAGE_SEQUENCE:
        if required not in emitted:
            errors.append(f"Required stage missing: {required}")
    return errors
