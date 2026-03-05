from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from shared.artifact_schemas import validate_artifact, with_artifact_header
from shared.event_schemas import append_event, build_event, validate_event
from shared.role_policy_schemas import decide_tool_access
from shared.stage_contracts import STAGE_SEQUENCE, validate_stage_sequence


class RuntimeContractsTests(unittest.TestCase):
    def test_artifact_header_contract(self) -> None:
        payload = with_artifact_header({"hello": "world"}, request_id="req-1", correlation_id="corr-1")
        self.assertEqual(validate_artifact(payload), [])

    def test_event_contract_and_append(self) -> None:
        event = build_event(
            request_id="req-1",
            correlation_id="corr-1",
            stage="plan_ingested",
            role="pm",
            decision="allow",
        )
        self.assertEqual(validate_event(event), [])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worklog.jsonl"
            append_event(path, event)
            self.assertTrue(path.exists())

    def test_policy_decision_denied_when_capability_missing(self) -> None:
        decision = decide_tool_access("pm", "shell")
        self.assertFalse(decision.allowed)

    def test_stage_sequence_contract(self) -> None:
        self.assertEqual(validate_stage_sequence(STAGE_SEQUENCE), [])


if __name__ == "__main__":
    unittest.main()
