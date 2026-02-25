import os
from openai import AzureOpenAI


class ConfigError(RuntimeError):
    pass


def _require_env(name: str, *, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or not value.strip():
        raise ConfigError(
            f"Missing required environment variable '{name}'. "
            "Set it before running orchestrator.py."
        )
    return value.strip()


def _build_client() -> AzureOpenAI:
    api_key = _require_env("AZURE_OPENAI_KEY")
    endpoint = _require_env(
        "AZURE_OPENAI_ENDPOINT", default="https://fullstackdevclinigma.openai.azure.com"
    )
    api_version = _require_env("AZURE_OPENAI_API_VERSION", default="2025-04-01-preview")
    return AzureOpenAI(
        api_key=api_key,
        api_version=api_version,
        azure_endpoint=endpoint,
    )


client = _build_client()