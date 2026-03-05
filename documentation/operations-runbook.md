# Operations Runbook

## Standard Lifecycle

1. Run PM clarification and plan normalization.
2. Approve plan and select deterministic pack.
3. Start OpenHands dispatcher loop.
4. Execute role stages with policy enforcement.
5. Validate gates and publish final report.

## Resume Procedure

- Use `request_id` to load latest persisted state.
- Verify last successful stage from `worklog.jsonl`.
- Resume from next deterministic stage.
- Preserve `correlation_id` lineage for audit continuity.

## Retry Procedure

- Retry is allowed only within stage budget.
- Every retry must append a reasoned decision event.
- Budget exhaustion triggers escalation.

## Rollback Controls

- Runtime rollback: restore previous executor path using a feature toggle.
- Artifact rollback: keep previous run artifacts immutable; append correction runs.
- Documentation rollback: restore removed docs through git history.

## Smoke Verification

- Run orchestrator flow tests.
- Run OpenHands runtime contract tests.
- Confirm `.ai/` artifact set is generated with valid schemas.
