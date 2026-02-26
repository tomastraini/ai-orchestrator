from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, Callable, Dict, List, Optional

from config import client
from services.pm.dev_handoff_store import DevHandoffStore, build_dev_handoff
from services.pm.pm_context_store import PMContextStore
from services.workspace.project_index import rank_candidate_files, scan_workspace_context
from shared.pathing import canonical_projects_path
from shared.schemas import PlanJSON, validate_plan_json


class PMServiceError(RuntimeError):
    pass


RoundAnswerFn = Callable[[str, int, int], str]
Checklist = Dict[str, str]


def _contains_any(text: str, needles: List[str]) -> bool:
    return any(needle in text for needle in needles)


def _infer_checklist_from_text(
    text: str, preselected_project_ref: Optional[Dict[str, str]]
) -> Checklist:
    req = (text or "").strip().lower()
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
    else:
        inferred["architecture"] = "custom"

    if _contains_any(req, ["no backend", "without backend", "frontend-only", "frontend only"]):
        inferred["backend_required"] = "no"
    elif _contains_any(req, ["backend", "api", "server", "nest", "express", "fastapi", "django"]):
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
    return canonical_projects_path(path, default_path)


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
        # v2 clean-break: do not hardcode front/back/database structure.
        target_files.append(
            {
                "file_name": "README.md",
                "expected_path_hint": f"{project_root}/README.md",
                "modification_type": "create",
                "details": "Document scope, setup, run instructions, and acceptance criteria.",
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


def _looks_like_scaffold_command(command: str) -> bool:
    low = (command or "").lower()
    return (
        "create-react-app" in low
        or "create-vite" in low
        or ("npm create" in low and "vite" in low)
        or ("npm init" in low and "vite" in low)
        or ("dotnet new" in low)
    )


def _normalize_bootstrap_commands(plan: Dict[str, Any]) -> Dict[str, Any]:
    commands = plan.get("bootstrap_commands")
    if not isinstance(commands, list):
        return plan
    project_ref = plan.get("project_ref") if isinstance(plan.get("project_ref"), dict) else {}
    project_name = str(project_ref.get("name") or "project").strip()
    project_root = _ensure_projects_rooted_path(project_ref.get("path_hint"), project_name)
    normalized: List[Dict[str, str]] = []
    for raw in commands:
        if not isinstance(raw, dict):
            continue
        cwd = str(raw.get("cwd", "")).strip()
        command = str(raw.get("command", "")).strip()
        purpose = str(raw.get("purpose", "bootstrap")).strip() or "bootstrap"
        if not cwd:
            if _looks_like_scaffold_command(command) and re.search(r"\bprojects/[A-Za-z0-9._-]+\b", command):
                cwd = "projects"
            elif _looks_like_scaffold_command(command):
                cwd = project_root
            else:
                cwd = project_root
        cwd = canonical_projects_path(cwd, project_root)
        normalized.append({"cwd": cwd, "command": command, "purpose": purpose})
    plan["bootstrap_commands"] = normalized
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
- Be framework-agnostic by default. Infer suitable stack from requirement and workspace context.
- New projects must be rooted at projects/<name>.
- Prefer deterministic non-interactive bootstrap commands, but avoid hardcoding framework assumptions.
- Ask clarification questions dynamically (no hardcoded script). Questions must be concise and answerable.
- Respect any preselected project_ref from user-confirmed routing.
- You are a senior PM at a large company. Produce an implementation-ready contract for senior developers.
- Include explicit code review guidelines and acceptance criteria tied to user intent.
- Encourage deep discovery over the workspace context before finalizing the plan.

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
    "stack": {{"frontend": "string", "backend": "string or null", "language_preferences": ["string"]}},
    "pm_checklist": {{
      "project_scope": "new_project|existing_project",
      "architecture": "string",
      "backend_required": "yes|no",
      "database_required": "yes|no"
    }},
    "bootstrap_commands": [{{"cwd": "string", "command": "string", "purpose": "string"}}],
    "target_files": [{{"file_name": "string", "expected_path_hint": "string", "modification_type": "string", "details": "string"}}],
    "constraints": ["string"],
    "validation": ["string"],
    "clarification_summary": ["string"],
    "review_guidelines": ["string"],
    "technical_preferences": {{"any_key": "any_value"}},
    "discovery_hints": ["string"],
    "product_contract": {{
      "goals": ["string"],
      "acceptance_criteria": ["string"],
      "non_goals": ["string"]
    }},
    "ambiguities": ["string"]
  }}
}}

Rules:
- {force_line}
- If status=needs_clarification => provide exactly one concrete question and plan must be null.
- If status=final_plan => question must be empty.
- No extra keys.
"""


def _parse_wrapper_json(raw_text: str) -> Dict[str, Any]:
    try:
        payload = json.loads(raw_text)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass

    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start >= 0 and end > start:
        maybe_json = raw_text[start : end + 1]
        try:
            payload = json.loads(maybe_json)
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
    raise PMServiceError(f"Failed to parse PM wrapper JSON:\n{raw_text}")


def _request_model_decision(
    requirement: str,
    rounds: List[Dict[str, str]],
    repo_name: str,
    checklist: Checklist,
    forced_project_ref: Optional[Dict[str, str]],
    *,
    force_final: bool,
    workspace_context: Dict[str, Any],
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
        "inferred_checklist": checklist,
        "preselected_project_ref": forced_project_ref or None,
        "workspace_context": workspace_context,
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
    payload = _parse_wrapper_json(raw_text)

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

    checklist = _infer_checklist_from_text(requirement, preselected_project_ref)
    for entry in rounds:
        checklist.update(
            _infer_checklist_from_text(
                f"{str(entry.get('question', ''))} {str(entry.get('answer', ''))}",
                preselected_project_ref,
            )
        )

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    workspace_context = scan_workspace_context(repo_root, file_limit=500)
    candidate_files = rank_candidate_files(requirement, workspace_context.get("sampled_files", []), top_k=80)
    workspace_context["ranked_prompt_candidates"] = candidate_files

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
            workspace_context=workspace_context,
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
            checklist = _infer_checklist_from_text(requirement, preselected_project_ref)
            for entry in rounds:
                checklist.update(
                    _infer_checklist_from_text(
                        f"{str(entry.get('question', ''))} {str(entry.get('answer', ''))}",
                        preselected_project_ref,
                    )
                )
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
        if "review_guidelines" not in plan or not isinstance(plan.get("review_guidelines"), list):
            plan["review_guidelines"] = [
                "No placeholder-only code or unresolved TODO markers in shipped files",
                "Changes must compile/build in the selected stack",
                "Each acceptance criterion must map to at least one implemented file or command check",
            ]
        if "technical_preferences" not in plan or not isinstance(plan.get("technical_preferences"), dict):
            plan["technical_preferences"] = {
                "autonomy_mode": "llm_first_with_guardrails",
                "scope_boundary": "projects_only_without_explicit_approval_outside_scope",
            }
        if "discovery_hints" not in plan or not isinstance(plan.get("discovery_hints"), list):
            plan["discovery_hints"] = [
                "Search repository for related symbols before editing",
                "Rank candidate files by likelihood of relevance to requirement",
            ]
        if "product_contract" not in plan or not isinstance(plan.get("product_contract"), dict):
            plan["product_contract"] = {
                "goals": [f"Deliver requested outcome: {requirement.strip()}"],
                "acceptance_criteria": [str(x) for x in plan.get("validation", [])] or ["Feature works as requested."],
                "non_goals": [],
            }
        if "ambiguities" not in plan or not isinstance(plan.get("ambiguities"), list):
            plan["ambiguities"] = []

        pm_checklist = plan.get("pm_checklist") if isinstance(plan.get("pm_checklist"), dict) else {}
        plan["pm_checklist"] = {
            "project_scope": str(checklist.get("project_scope") or pm_checklist.get("project_scope") or "new_project"),
            "architecture": str(checklist.get("architecture") or pm_checklist.get("architecture") or "custom"),
            "backend_required": str(checklist.get("backend_required") or pm_checklist.get("backend_required") or "yes"),
            "database_required": str(checklist.get("database_required") or pm_checklist.get("database_required") or "no"),
        }
        plan = _normalize_new_project_plan(plan, checklist)
        plan = _normalize_existing_project_plan(plan, preselected_project_ref)
        plan = _normalize_bootstrap_commands(plan)
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
        DevHandoffStore(repo_root=repo_root).write_latest(handoff)
        return plan

    raise PMServiceError("PM did not return a final plan within allowed clarification rounds.")
