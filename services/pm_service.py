from __future__ import annotations

import json
import os
import uuid
from typing import Any, Callable, Dict, List, Optional

from config import client
from shared.schemas import validate_plan_json, PlanJSON
from services.pm_context_store import PMContextStore


class PMServiceError(RuntimeError):
    pass


RoundAnswerFn = Callable[[str, int, int], str]


def _extract_output_text(response: Any) -> str:
    content = None
    for item in response.output:
        if item.type != "message":
            continue
        for part in item.content:
            if part.type == "output_text":
                content = part.text
                break
    if content is None:
        raise PMServiceError(f"Could not extract model text from response: {response}")
    return content


def _deployment_name() -> str:
    return os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.1-codex-mini")


def _ensure_responses_api_available() -> None:
    if hasattr(client, "responses"):
        return
    raise PMServiceError(
        "Installed OpenAI SDK does not support Azure Responses API on this client. "
        "Install dependencies with `pip install -r requirements.txt` "
        "and confirm openai==1.66.3 is installed."
    )


def _make_decision_prompt(repo_name: str, *, force_final: bool) -> str:
    force_line = (
        "You must return status='final_plan'. Do not ask clarification questions."
        if force_final
        else "Use status='needs_clarification' only when project reference/stack is genuinely ambiguous."
    )
    return f"""
You are a senior technical product manager.
Repository: {repo_name}

Your job:
1) Decide whether requirement references an existing project or a new project.
2) If ambiguous, ask exactly one focused clarification question.
3) If clear enough, produce a strict execution plan JSON.

Defaults and policies:
- If stack is unspecified, default to React + NestJS with TypeScript preference.
- Prefer TypeScript unless requirement explicitly says otherwise.
- For new React app creation, prefer: npx create-react-app <name> --template typescript
- For new NestJS app creation, prefer: nest new <name> (fallback: npx @nestjs/cli new <name>)
- Include command cwd values and concise purpose.
- Never output markdown.
- Never output explanatory prose.

Output JSON wrapper schema (exact keys only):
{{
  "status": "needs_clarification" | "final_plan",
  "question": "non-empty string when status=needs_clarification, else empty string",
  "hypothesis": {{
    "project_mode": "new_project" | "existing_project",
    "project_ref_name": "string",
    "project_ref_path_hint": "string or null",
    "frontend_guess": "string",
    "backend_guess": "string or null"
  }},
  "plan": {{
    "summary": "string",
    "project_mode": "new_project" | "existing_project",
    "project_ref": {{
      "name": "string",
      "path_hint": "string or null"
    }},
    "stack": {{
      "frontend": "string",
      "backend": "string or null",
      "language_preferences": ["TypeScript"]
    }},
    "bootstrap_commands": [
      {{
        "cwd": "string",
        "command": "string",
        "purpose": "string"
      }}
    ],
    "target_files": [
      {{
        "file_name": "string",
        "expected_path_hint": "string",
        "modification_type": "string",
        "details": "string"
      }}
    ],
    "constraints": ["string"],
    "validation": ["string"],
    "clarification_summary": ["string"]
  }}
}}

Rules:
- {force_line}
- If status=needs_clarification, set plan to null.
- If status=final_plan, question must be empty string and plan must be complete.
- No extra keys anywhere.
"""


def _request_model_decision(
    requirement: str,
    rounds: List[Dict[str, str]],
    repo_name: str,
    *,
    force_final: bool,
) -> Dict[str, Any]:
    system_prompt = _make_decision_prompt(repo_name, force_final=force_final)
    user_payload = {
        "requirement": requirement,
        "clarification_rounds": rounds,
        "max_clarification_rounds": 3,
        "force_final": force_final,
    }

    try:
        _ensure_responses_api_available()
        response = client.responses.create(
            model=_deployment_name(),
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
        )
    except Exception as e:
        raise PMServiceError(f"Azure call failed: {e}") from e

    raw_text = _extract_output_text(response)
    try:
        payload = json.loads(raw_text)
    except Exception as e:
        raise PMServiceError(
            f"Failed to parse PM wrapper JSON:\n{raw_text}"
        ) from e

    if not isinstance(payload, dict):
        raise PMServiceError(f"PM wrapper output must be an object. Got: {payload!r}")
    return payload


def create_plan(
    requirement: str,
    repo_name: str = "ai-orchestrator",
    request_id: Optional[str] = None,
    context_store: Optional[PMContextStore] = None,
    ask_user: Optional[RoundAnswerFn] = None,
    max_rounds: int = 3,
) -> PlanJSON:
    """
    PM brain with clarification loop (max_rounds) and strict plan output validation.
    """
    if not requirement or not requirement.strip():
        raise PMServiceError("Requirement cannot be empty.")

    if request_id is None:
        request_id = str(uuid.uuid4())

    if context_store is None:
        repo_root = os.path.dirname(os.path.dirname(__file__))
        context_store = PMContextStore(repo_root=repo_root)

    context = context_store.load_context(request_id=request_id, original_requirement=requirement)
    existing_rounds_raw = context.get("rounds", [])
    rounds: List[Dict[str, str]] = []
    if isinstance(existing_rounds_raw, list):
        for entry in existing_rounds_raw:
            if isinstance(entry, dict):
                q = str(entry.get("question", "")).strip()
                a = str(entry.get("answer", "")).strip()
                if q and a:
                    rounds.append({"question": q, "answer": a})

    for round_index in range(max_rounds + 1):
        force_final = round_index >= max_rounds
        decision = _request_model_decision(
            requirement=requirement,
            rounds=rounds,
            repo_name=repo_name,
            force_final=force_final,
        )

        status = decision.get("status")
        hypothesis = decision.get("hypothesis")
        if isinstance(hypothesis, dict):
            context_store.update_hypothesis(request_id=request_id, hypothesis=hypothesis)

        if status == "needs_clarification" and not force_final:
            question = str(decision.get("question", "")).strip()
            if not question:
                raise PMServiceError("PM requested clarification but did not provide a question.")
            if ask_user is None:
                raise PMServiceError(
                    "PM requires clarification but no ask_user callback was provided."
                )
            answer = ask_user(question, round_index + 1, max_rounds).strip()
            if not answer:
                raise PMServiceError("Clarification answer cannot be empty.")
            context_store.append_round(request_id=request_id, question=question, answer=answer)
            rounds.append({"question": question, "answer": answer})
            continue

        plan = decision.get("plan")
        if not isinstance(plan, dict):
            raise PMServiceError(
                f"PM final payload must include plan object. Raw decision: {decision}"
            )

        if "clarification_summary" not in plan:
            plan["clarification_summary"] = [
                f"Q: {entry['question']} | A: {entry['answer']}" for entry in rounds
            ]

        ok, errors = validate_plan_json(plan, requirement=requirement)
        if not ok:
            raise PMServiceError(
                "PM plan JSON failed validation:\n- "
                + "\n- ".join(errors)
                + "\n\nRaw Plan:\n"
                + json.dumps(plan, indent=2)
            )

        context_store.save_final_plan(request_id=request_id, plan=plan)
        return plan

    raise PMServiceError("PM did not return a final plan within allowed clarification rounds.")