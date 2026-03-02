# ai-orchestrator

`ai-orchestrator` is a PM-first workflow that gathers requirements, produces an implementation-ready plan, and delegates implementation to Claude Code CLI.

## Architecture

- `PM` creates a strict plan contract (`services/pm/pm_service.py`)
- `Orchestrator` handles plan approval and execution (`orchestrator.py`)
- `Execution` invokes Claude Code CLI (`services/execution/claude_cli_executor.py`)
- Run artifacts are written to `.orchestrator/runs/<request_id>/`

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

- `CLAUDE_CODE_CMD` (default: `claude`)
- `CLAUDE_CODE_ARGS` (default: `--print`)
- `CLAUDE_CODE_TIMEOUT_SECONDS` (default: `1800`)

Example (PowerShell):

```powershell
$env:AZURE_OPENAI_KEY="your-key"
$env:CLAUDE_CODE_CMD="claude"
$env:CLAUDE_CODE_ARGS="--print"
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

