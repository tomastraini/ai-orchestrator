# Plan Next Steps

## Completed Pivot Work

- Removed the full internal DEV runtime surface.
- Introduced `services/execution/claude_cli_executor.py`.
- Rewired `orchestrator.py` to PM -> approval -> Claude CLI flow.
- Removed PM dev-handoff package and placeholder PR package.

## Immediate Follow-Ups

1. Add richer executor error taxonomy (`not_installed`, `auth_failed`, `timeout`, `non_zero_exit`).
2. Add retry policy options for transient CLI failures.
3. Add a runbook for CLI auth and local setup troubleshooting.
4. Expand integration tests around real CLI command contracts.

## Medium-Term Enhancements

- Optional branch/PR automation as a separate module, not coupled to PM.
- Structured execution event stream for observability dashboards.
- Multi-repo support with per-project executor configuration.

## Long-Term Direction

- Keep PM as planning contract generator.
- Keep execution pluggable so Claude CLI can be replaced or augmented without touching PM logic.

