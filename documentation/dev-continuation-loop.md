# Execution Continuation Model

## Status

The legacy DEV continuation loop has been retired with the removal of `services/dev`.

## New Approach

Continuation is now handled at workflow level, not by an internal dev graph:

- PM can be re-run with a follow-up requirement.
- Orchestrator can execute latest or targeted plan request IDs.
- Claude CLI performs implementation per run.

Each execution remains isolated and produces artifacts under `.orchestrator/runs/<request_id>/`.

## Practical Iteration Pattern

1. Run `orchestrator.py --mode full` with a requirement.
2. Review output and logs.
3. Provide a new delta requirement.
4. Run again to generate a refined PM plan and re-execute via Claude CLI.

