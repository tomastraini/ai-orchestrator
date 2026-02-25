# ai-orchestrator

## Setup

### 1) Create and activate a virtual environment

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

### 3) Configure environment variables

Required:

- `AZURE_OPENAI_KEY`

Optional (defaults are provided in code):

- `AZURE_OPENAI_ENDPOINT` (default: `https://fullstackdevclinigma.openai.azure.com`)
- `AZURE_OPENAI_API_VERSION` (default: `2025-04-01-preview`)
- `AZURE_OPENAI_DEPLOYMENT` (default: `gpt-5.1-codex-mini`)

Windows (PowerShell):

```powershell
$env:AZURE_OPENAI_KEY="your-key"
$env:AZURE_OPENAI_ENDPOINT="https://fullstackdevclinigma.openai.azure.com"
$env:AZURE_OPENAI_API_VERSION="2025-04-01-preview"
$env:AZURE_OPENAI_DEPLOYMENT="gpt-5.1-codex-mini"
```

Ubuntu:

```bash
export AZURE_OPENAI_KEY="your-key"
export AZURE_OPENAI_ENDPOINT="https://fullstackdevclinigma.openai.azure.com"
export AZURE_OPENAI_API_VERSION="2025-04-01-preview"
export AZURE_OPENAI_DEPLOYMENT="gpt-5.1-codex-mini"
```

### 4) Run orchestrator

```bash
python orchestrator.py
```

## Diagnostics

Verify SDK version:

```bash
python -c "import openai; print(openai.__version__)"
```

Verify Azure client exposes Responses API:

```bash
python -c "from config import client; print(hasattr(client, 'responses'))"
```

If setup is incorrect, runtime errors should point to:

- missing env vars in `config.py`
- missing/old OpenAI SDK in `services/pm_service.py`
