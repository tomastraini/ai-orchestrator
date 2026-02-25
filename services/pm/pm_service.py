from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, Callable, Dict, List, Optional

from config import client
from shared.schemas import validate_plan_json, PlanJSON
from services.pm.pm_context_store import PMContextStore
from services.pm.dev_handoff_store import DevHandoffStore, build_dev_handoff


class PMServiceError(RuntimeError):
    pass


RoundAnswerFn = Callable[[str, int, int], str]
Checklist = Dict[str, str]

MANDATORY_CHECKLIST_QUESTIONS: List[tuple[str, str]] = [
    ("project_scope", "Is this for a new project or an existing project? (new/existing)"),
    ("architecture", "Should this be frontend-only or fullstack? (frontend/fullstack)"),
    ("backend_required", "Is a backend required? (yes/no)"),
    ("database_required", "Is a database required? (yes/no)"),
]


def _normalize_checklist_answer(field: str, answer: str) -> str:
    value = (answer or "").strip().lower()
    if field == "project_scope":
        if "existing" in value:
            return "existing_project"
        if "new" in value or "create" in value:
            return "new_project"
        return "unknown"
    if field == "architecture":
        if "full" in value:
            return "fullstack"
        if "front" in value:
            return "frontend_only"
        return "unspecified"
    if field in {"backend_required", "database_required"}:
        if value in {"yes", "y", "true", "required"}:
            return "yes"
        if value in {"no", "n", "false", "not required"}:
            return "no"
        return "unspecified"
    return "unspecified"


def _extract_checklist_from_rounds(rounds: List[Dict[str, str]]) -> Checklist:
    collected: Checklist = {}
    by_question = {
        question.lower(): field for field, question in MANDATORY_CHECKLIST_QUESTIONS
    }
    for round_entry in rounds:
        q = str(round_entry.get("question", "")).strip().lower()
        a = str(round_entry.get("answer", "")).strip()
        if not q or not a:
            continue
        field = by_question.get(q)
        if not field:
            continue
        collected[field] = _normalize_checklist_answer(field, a)
    return collected


def _missing_checklist_fields(checklist: Checklist) -> List[str]:
    missing: List[str] = []
    for field, _ in MANDATORY_CHECKLIST_QUESTIONS:
        if checklist.get(field) in {None, "", "unknown", "unspecified"}:
            missing.append(field)
    return missing


def _contains_any(text: str, needles: List[str]) -> bool:
    return any(needle in text for needle in needles)


def _infer_checklist_from_requirement(
    requirement: str, preselected_project_ref: Optional[Dict[str, str]]
) -> Checklist:
    req = (requirement or "").strip().lower()
    inferred: Checklist = {}

    if preselected_project_ref:
        inferred["project_scope"] = "existing_project"
    elif _contains_any(req, ["new project", "from scratch", "scaffold", "create "]):
        inferred["project_scope"] = "new_project"
    elif _contains_any(req, ["improve ", "update ", "enhance ", "refactor ", "existing project"]):
        inferred["project_scope"] = "existing_project"

    if _contains_any(req, ["frontend-only", "frontend only", "ui only", "without backend"]):
        inferred["architecture"] = "frontend_only"
    elif _contains_any(req, ["fullstack", "full stack"]):
        inferred["architecture"] = "fullstack"

    if _contains_any(req, ["no backend", "without backend", "frontend-only", "frontend only"]):
        inferred["backend_required"] = "no"
    elif _contains_any(
        req,
        [
            "backend",
            "api",
            "server",
            "nest",
            "express",
            "fastapi",
            "django",
            "flask",
            "ruby",
        ],
    ):
        inferred["backend_required"] = "yes"

    if _contains_any(req, ["no db", "no database", "without database"]):
        inferred["database_required"] = "no"
    elif re.search(r"\b(db|database|postgres|mysql|sqlite|mongo|redis)\b", req):
        inferred["database_required"] = "yes"

    if inferred.get("architecture") == "frontend_only" and "backend_required" not in inferred:
        inferred["backend_required"] = "no"
    if inferred.get("backend_required") == "no" and "database_required" not in inferred:
        inferred["database_required"] = "no"
    return inferred


def _slugify_project_name(name: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in name.strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    cleaned = cleaned.strip("-")
    return cleaned or "project"


def _ensure_projects_rooted_path(path: Optional[str], project_name: str) -> str:
    default_path = f"projects/{_slugify_project_name(project_name)}"
    if not isinstance(path, str) or not path.strip():
        return default_path
    normalized = path.replace("\\", "/").strip().lstrip("./")
    if normalized == "projects" or normalized.startswith("projects/"):
        return normalized
    return default_path


def _normalize_new_project_plan(
    plan: Dict[str, Any], checklist: Optional[Checklist] = None
) -> Dict[str, Any]:
    if plan.get("project_mode") != "new_project":
        return plan

    project_ref = plan.get("project_ref")
    if not isinstance(project_ref, dict):
        project_ref = {}
        plan["project_ref"] = project_ref

    project_name = str(project_ref.get("name") or "project")
    project_path = _ensure_projects_rooted_path(project_ref.get("path_hint"), project_name)
    project_ref["path_hint"] = project_path
    slug = _slugify_project_name(project_name)
    project_root = f"projects/{slug}"

    target_files = plan.get("target_files")
    if not isinstance(target_files, list):
        target_files = []
        plan["target_files"] = target_files

    checklist = checklist or {}
    if len(target_files) == 0:
        wants_frontend = checklist.get("architecture") in {"frontend_only", "fullstack"}
        wants_backend = checklist.get("backend_required") == "yes"
        wants_database = checklist.get("database_required") == "yes"

        if not wants_frontend and not wants_backend:
            # Keep sensible MVP default when checklist is incomplete.
            wants_frontend = True
            wants_backend = True

        if wants_frontend:
            target_files.append(
                {
                    "file_name": "front-end",
                    "expected_path_hint": f"{project_root}/front-end",
                    "modification_type": "create_directory",
                    "details": "Create frontend workspace directory.",
                }
            )
        if wants_backend:
            target_files.append(
                {
                    "file_name": "back-end",
                    "expected_path_hint": f"{project_root}/back-end",
                    "modification_type": "create_directory",
                    "details": "Create backend workspace directory.",
                }
            )
        if wants_database:
            target_files.append(
                {
                    "file_name": "database",
                    "expected_path_hint": f"{project_root}/database",
                    "modification_type": "create_directory",
                    "details": "Create database workspace directory.",
                }
            )
    return plan


def _normalize_existing_project_plan(
    plan: Dict[str, Any], forced_project_ref: Optional[Dict[str, str]]
) -> Dict[str, Any]:
    if not forced_project_ref:
        return plan
    if plan.get("project_mode") != "existing_project":
        return plan
    project_ref = plan.get("project_ref")
    if not isinstance(project_ref, dict):
        project_ref = {}
        plan["project_ref"] = project_ref
    forced_name = str(forced_project_ref.get("name", "")).strip()
    forced_path = str(forced_project_ref.get("path_hint", "")).strip()
    if forced_name:
        project_ref["name"] = forced_name
    if forced_path:
        project_ref["path_hint"] = forced_path
    return plan


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
You are a senior technical product manager for repository: {repo_name}.
Return strict JSON only.

Policies:
- Default stack: React + NestJS, TypeScript preference.
- New projects must be rooted at projects/<name>.
- Prefer deterministic non-interactive bootstrap commands.
- Respect mandatory checklist and any preselected project_ref from user-confirmed routing.

Wrapper format:
{{
  "status": "needs_clarification" | "final_plan",
  "question": "string",
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
    "project_ref": {{"name": "string", "path_hint": "string or null"}},
    "stack": {{"frontend": "string", "backend": "string or null", "language_preferences": ["TypeScript"]}},
    "pm_checklist": {{
      "project_scope": "new_project|existing_project",
      "architecture": "frontend_only|fullstack",
      "backend_required": "yes|no",
      "database_required": "yes|no"
    }},
    "bootstrap_commands": [{{"cwd": "string", "command": "string", "purpose": "string"}}],
    "target_files": [{{"file_name": "string", "expected_path_hint": "string", "modification_type": "string", "details": "string"}}],
    "constraints": ["string"],
    "validation": ["string"],
    "clarification_summary": ["string"]
  }}
}}

Rules:
- {force_line}
- If status=needs_clarification => plan must be null.
- If status=final_plan => question must be empty.
- No extra keys.
"""


def _request_model_decision(
    requirement: str,
    rounds: List[Dict[str, str]],
    repo_name: str,
    checklist: Checklist,
    forced_project_ref: Optional[Dict[str, str]],
    *,
    force_final: bool,
) -> Dict[str, Any]:
    system_prompt = _make_decision_prompt(repo_name, force_final=force_final)
    recent_rounds = rounds[-2:] if len(rounds) > 2 else rounds
    summary_rounds = [
        f"Q: {str(entry.get('question', '')).strip()} | A: {str(entry.get('answer', '')).strip()}"
        for entry in rounds[-6:]
    ]
    user_payload = {
        "requirement": requirement,
        "clarification_rounds_recent": recent_rounds,
        "clarification_rounds_summary": summary_rounds,
        "clarification_round_count": len(rounds),
        "mandatory_checklist": checklist,
        "preselected_project_ref": forced_project_ref or None,
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
    preselected_project_ref: Optional[Dict[str, str]] = None,
    max_model_calls_per_run: int = 4,
) -> PlanJSON:
    """
    PM brain with clarification loop (max_rounds) and strict plan output validation.
    """
    if not requirement or not requirement.strip():
        raise PMServiceError("Requirement cannot be empty.")

    if request_id is None:
        request_id = str(uuid.uuid4())

    if context_store is None:
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
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

    checklist = _infer_checklist_from_requirement(requirement, preselected_project_ref)
    checklist.update(_extract_checklist_from_rounds(rounds))
    for field, question in MANDATORY_CHECKLIST_QUESTIONS:
        if field in checklist and checklist[field] not in {"unknown", "unspecified"}:
            continue
        if ask_user is None:
            raise PMServiceError(
                "PM mandatory checklist requires ask_user callback for stack-critical questions."
            )
        answer = ask_user(question, 0, max_rounds).strip()
        if not answer:
            raise PMServiceError("Mandatory checklist answer cannot be empty.")
        context_store.append_round(request_id=request_id, question=question, answer=answer)
        rounds.append({"question": question, "answer": answer})
        checklist[field] = _normalize_checklist_answer(field, answer)

    missing = _missing_checklist_fields(checklist)
    if missing:
        raise PMServiceError(
            f"PM mandatory checklist incomplete for fields: {', '.join(missing)}."
        )

    model_calls = 0

    for round_index in range(max_rounds + 1):
        if model_calls >= max_model_calls_per_run:
            raise PMServiceError(
                f"PM model-call budget reached ({max_model_calls_per_run}). "
                "Provide more explicit requirement details and retry."
            )
        force_final = round_index >= max_rounds
        decision = _request_model_decision(
            requirement=requirement,
            rounds=rounds,
            repo_name=repo_name,
            checklist=checklist,
            forced_project_ref=preselected_project_ref,
            force_final=force_final,
        )
        model_calls += 1

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
            checklist = _infer_checklist_from_requirement(requirement, preselected_project_ref)
            checklist.update(_extract_checklist_from_rounds(rounds))
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

        plan["pm_checklist"] = {
            "project_scope": checklist.get("project_scope", "unknown"),
            "architecture": checklist.get("architecture", "unspecified"),
            "backend_required": checklist.get("backend_required", "unspecified"),
            "database_required": checklist.get("database_required", "unspecified"),
        }
        plan = _normalize_new_project_plan(plan, checklist)
        plan = _normalize_existing_project_plan(plan, preselected_project_ref)
        ok, errors = validate_plan_json(plan, requirement=requirement)
        if not ok:
            raise PMServiceError(
                "PM plan JSON failed validation:\n- "
                + "\n- ".join(errors)
                + "\n\nRaw Plan:\n"
                + json.dumps(plan, indent=2)
            )

        context_store.save_final_plan(request_id=request_id, plan=plan)
        handoff = build_dev_handoff(request_id=request_id, plan=plan, rounds=rounds)
        context_store.save_dev_handoff(request_id=request_id, handoff=handoff)
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        DevHandoffStore(repo_root=repo_root).write_latest(handoff)
        return plan

    raise PMServiceError("PM did not return a final plan within allowed clarification rounds.")
