# Dispatcher And Minion Policy

## Required Roles

- `pm`: produces normalized plan contract.
- `planner`: converts plan into executable task graph.
- `builder`: applies implementation changes.
- `validator`: runs checks and verifies acceptance criteria.
- `finalizer`: compiles final report and PR-ready payload.

## Optional Specialists

- `test_fixer`
- `dependency_fixer`
- `docs_writer`
- `security_reviewer`

Dispatcher invokes specialists only when policy predicates match.

## Dispatch Contract

Each dispatch decision includes:

- `request_id`
- `correlation_id`
- `from_stage`
- `to_role`
- `trigger`
- `policy_rule`
- `budget_remaining`
- `decision` (`allow`, `deny`, `escalate`, `retry`)
- `provenance`

## Tool Least-Privilege Matrix

| Role | Read Files | Write Files | Shell | Browser | MCP/OpenHands |
| --- | --- | --- | --- | --- | --- |
| pm | yes | no | no | no | limited |
| planner | yes | no | limited | no | limited |
| builder | yes | yes | yes | limited | yes |
| validator | yes | no | yes | yes | yes |
| finalizer | yes | yes | limited | no | limited |
| security_reviewer | yes | no | yes | no | yes |

## Escalation Rules

- Retry within role until role budget reaches zero.
- Escalate to `planner` for re-plan when repeated validator failures occur.
- Escalate to human only for unresolved ambiguity or policy deadlock.
- Stop run on critical contract violation (invalid schema/event order).
