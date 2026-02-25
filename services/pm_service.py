# services/pm_service.py

from __future__ import annotations

import json
from typing import Dict

from config import client
from shared.schemas import validate_plan_json, PlanJSON


class PMServiceError(RuntimeError):
    pass


def create_plan(requirement: str, repo_name: str = "Clinigma-Transcripts") -> PlanJSON:
    """
    PM Brain using Azure OpenAI Responses API (GPT-5.1 compatible).
    Strict JSON contract enforced via prompting.
    """

    system_prompt = f"""
You are a senior technical product manager.

You convert vague engineering requirements into structured implementation plans.

You MUST return ONLY valid JSON.

Schema:

{{
  "summary": "...",
  "target_files": [
    {{
      "file_name": "...",
      "expected_path_hint": "...",
      "modification_type": "...",
      "details": "..."
    }}
  ],
  "constraints": ["...", "..."],
  "validation": ["...", "..."]
}}

Rules:
- Output must be valid JSON.
- No markdown.
- No explanation.
- No extra keys.
- target_files must not be empty.
- Prefer minimal changes.
- Never rewrite entire files.
- Include build validation where appropriate.

Repository: {repo_name}
"""

    try:
        response = client.responses.create(
            model="gpt-5.1-codex-mini",  # must match deployment name
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": requirement},
            ],
        )
    except Exception as e:
        raise PMServiceError(f"Azure call failed: {e}") from e

    # Extract the assistant message (skip reasoning blocks)
    content = None
    for item in response.output:
        if item.type == "message":
            for part in item.content:
                if part.type == "output_text":
                    content = part.text
                    break

    if content is None:
        raise PMServiceError(f"Could not extract model text from response: {response}")

    # Parse JSON
    try:
        plan = json.loads(content)
    except Exception:
        raise PMServiceError(
            f"Failed to parse JSON from model output:\n\n{content}"
        )

    # Validate schema contract
    ok, errors = validate_plan_json(plan)
    if not ok:
        raise PMServiceError(
            "PM plan JSON failed validation:\n- "
            + "\n- ".join(errors)
            + "\n\nRaw Output:\n"
            + content
        )

    return plan
