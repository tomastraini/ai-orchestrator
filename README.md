# ai-orchestrator

`ai-orchestrator` is an OpenHands-first autonomous SDLC orchestrator. It accepts requirements, runs PM clarification, dispatches role-based minions, validates outcomes, and produces PR-ready artifacts with deterministic evidence.

## Architecture

- `PM` normalizes requirement contracts (`services/pm/pm_service.py`)
- `Orchestrator` coordinates planning, approval, and multi-role execution (`orchestrator.py`)
- `Execution` runs OpenHands-first dispatcher/minion flow (`services/execution/openhands_runtime.py`)
- Runtime contracts live in `shared/` (artifacts, events, stage, and role policy schemas)
- Stack-specific behavior is isolated in `deterministics/`

Canonical architecture docs:

- `documentation/README.md`
- `documentation/architecture-overview.md`
- `documentation/dispatcher-and-minion-policy.md`
- `documentation/deterministics-pack-guide.md`
- `documentation/artifact-schema-guide.md`

## Setup

### 1) Create and activate virtual environment

Windows (PowerShell):

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Ubuntu:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2) Install dependencies

```bash
pip install -r requirements.txt
```

### 3) Configure environment

Required for PM model calls:

- `AZURE_OPENAI_KEY`

Optional:

- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_API_VERSION`
- `AZURE_OPENAI_DEPLOYMENT`

Required for execution:

- `OPENHANDS_CMD` (default: `openhands`)
- `OPENHANDS_ARGS` (default: `run`)
- `OPENHANDS_TIMEOUT_SECONDS` (default: `1800`)
- `OPENHANDS_ENABLE_SPECIALISTS` (default: `true`)
- `OPENHANDS_TOOL_POLICY_MODE` (default: `enforce`)

Fallback rollback toggle:

- `OPENHANDS_FALLBACK_TO_CLAUDE` (default: `false`)

Example (PowerShell):

```powershell
$env:AZURE_OPENAI_KEY="your-key"
$env:OPENHANDS_CMD="openhands"
$env:OPENHANDS_ARGS="run"
```

## Run

Generate and execute plan:

```bash
python orchestrator.py --mode full --requirement "Build a simple dashboard app"
```

Generate plan only:

```bash
python orchestrator.py --mode plan --requirement "Build a simple dashboard app"
```

Execute latest approved plan:

```bash
python orchestrator.py --mode execute --from-latest
```

