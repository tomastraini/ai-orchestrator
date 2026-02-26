# AI Orchestrator TLDR

## What this system does

This project is an "AI project coordinator" that takes a request like:

- "Build a new app"
- "Improve an existing project"

Then it creates a plan and executes that plan in a controlled way.

Think of it as two teammates working together:

- a **Project Manager (PM)** that plans
- a **Developer (Dev)** that executes

---

## The simple version in 7 steps

1. You give a requirement in plain language.
2. The PM analyzes the repository and asks clarification questions if needed.
3. The PM produces a structured plan (what to build, where, constraints, checks).
4. You approve the plan.
5. The Dev runs a step-by-step execution workflow.
6. The Dev runs validation and final checks.
7. The system saves results and logs so you can inspect or resume later.

---

## How it is organized (without deep jargon)

- `orchestrator.py` is the conductor.
- PM code in `services/pm/` creates a reliable plan.
- Dev code in `services/dev/` executes that plan in phases.
- Shared contracts in `shared/` keep data predictable.
- `.orchestrator/` stores saved context, handoff data, and run artifacts.

---

## Why there are two phases (PM then Dev)

Splitting the workflow gives better outcomes:

- PM phase reduces ambiguity before execution starts.
- Dev phase focuses on implementation reliability (retries, safety checks, final validation).
- If anything fails, evidence is already captured for fast troubleshooting.

---

## What happens when commands fail

The Dev side does not just fail immediately. It tries to recover:

1. retry using deterministic command rewrites
2. optionally reserve a final LLM-assisted correction path
3. log attempts, failure category, and timing

This gives practical resilience for real-world CLI execution.

---

## Safety and control in plain terms

- Commands are restricted to the `projects/` scope.
- Risky command patterns can be blocked or require confirmation.
- Constraints from the PM plan are enforced (for example, "no git push").
- Sensitive log patterns (api keys, tokens, passwords) are redacted.

---

## Why resume works

The orchestrator saves state on disk:

- PM context (questions, answers, final plan)
- PM -> Dev handoff contract
- per-run artifacts (events, outcomes, summary)

Because these are persisted, execution can continue from latest known state instead of starting over every time.

---

## Who should read what

- Read `documentation/TLDR.md` if you want a fast understanding.
- Read `documentation/architecture-deep-dive.md` if you need full technical details, extension points, and troubleshooting depth.

---

## Quick start

```bash
python orchestrator.py --mode full --requirement "Describe your feature"
```

Other common modes:

- Plan only:
  - `python orchestrator.py --mode plan --requirement "Describe your feature"`
- Execute latest plan/handoff:
  - `python orchestrator.py --mode execute --from-latest`

---

## 5 key takeaways

1. It is a two-role system: planning first, execution second.
2. Plans are validated before execution, which lowers execution risk.
3. Execution is phase-based, observable, and retry-aware.
4. Safety boundaries and constraints are enforced during command runs.
5. Persistent artifacts make debugging and resume practical.
