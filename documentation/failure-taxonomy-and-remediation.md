# Failure Taxonomy And Remediation

## Failure Classes

- `ambiguity_unresolved`: PM cannot normalize requirement without human input.
- `policy_denied`: role attempted tool/action not authorized by policy.
- `tool_runtime_failure`: OpenHands or shell/tool invocation failed.
- `validation_failed`: tests/lint/gates failed in validator stage.
- `artifact_contract_violation`: generated artifact failed schema checks.
- `retry_budget_exhausted`: retries consumed without successful exit.
- `resume_inconsistency`: persisted state conflicts with expected stage ordering.

## Severity Levels

- `critical`: stop run immediately; manual intervention required.
- `high`: automatic escalation and re-plan required.
- `medium`: retry allowed within budget.
- `low`: warning recorded; continue.

## Remediation Playbooks

- Policy denial: adjust role assignment or modify policy profile in deterministic pack.
- Validation failure: invoke `test_fixer` or `dependency_fixer`, then rerun validator.
- Contract violation: regenerate artifact and validate schema before proceeding.
- Resume inconsistency: replay worklog to last valid checkpoint and continue from checkpoint.

## Required Evidence

Every failure event records:

- error class
- severity
- triggering stage
- remediation action
- outcome
- operator escalation flag
