# TLDR

## Pivot Summary

- Internal DEV orchestration was removed.
- PM still gathers requirements and outputs a strict plan.
- Execution now delegates to Claude Code CLI through `services/execution/claude_cli_executor.py`.

## Current Flow

1. User requirement enters `orchestrator.py`.
2. PM builds and validates plan via `services/pm/pm_service.py`.
3. User approves plan.
4. Claude CLI executes implementation.
5. Artifacts are stored in `.orchestrator/runs/<request_id>/`.

## Why This Pivot

- Reduce internal orchestration complexity.
- Rely on a mature external coding runtime.
- Improve maintainability by shrinking custom DEV logic.

