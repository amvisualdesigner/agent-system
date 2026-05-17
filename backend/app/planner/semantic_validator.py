import os
from typing import Any

from app.semantic_engine import retrieve, load_semantic_entries
from app.config.settings import settings
from app.executor.skill_resolver import SKILL_REGISTRY

UI_PATH_PATTERNS = ["components/", "layouts/", "patterns/"]
ALLOWED_PATH_PATTERNS = ["data_contracts/", "architecture/"]

_SEMANTIC_ENTRIES = None


def _get_semantic_entries():
    global _SEMANTIC_ENTRIES
    if _SEMANTIC_ENTRIES is None:
        _SEMANTIC_ENTRIES = load_semantic_entries(settings.SEMANTIC_DIR)
    return _SEMANTIC_ENTRIES


def _get_entity_names() -> set[str]:
    return {e.name.lower() for e in _get_semantic_entries()}


def _is_ui_file(file_path: str, entity_names: set[str]) -> bool:
    if any(p in file_path for p in UI_PATH_PATTERNS):
        return True
    stem = os.path.splitext(os.path.basename(file_path))[0].lower()
    stem = stem.replace("_", "").replace("-", "")
    for name in entity_names:
        clean = name.replace("_", "").replace("-", "").replace(".", "")
        if stem == clean or stem.endswith(clean):
            return True
    return False


def skills_exist() -> bool:
    entries = _get_semantic_entries()
    return any(e.type == "skill" for e in entries)


def _select_best_skill(task: str):
    entries = _get_semantic_entries()
    skills = [e for e in entries if e.type == "skill" and e.name in SKILL_REGISTRY]
    if not skills:
        skills = [e for e in entries if e.type == "skill"]
        if not skills:
            return None
        return skills[0].name
    matches = retrieve(task, skills, top_k=1, min_score=0)
    if matches:
        return matches[0].name
    return skills[0].name


def validate_semantic_plan(plan: dict, task_mode: str, task: str = "") -> dict:
    if task_mode != "semantic_skill":
        return plan

    if not skills_exist():
        return plan

    entity_names = _get_entity_names()
    actions = plan.get("actions", [])
    validated = []

    for action in actions:
        if action.get("type") == "use_skill":
            validated.append(action)
            continue

        if action.get("type") not in ("create", "modify"):
            validated.append(action)
            continue

        file_path = action.get("file_path", "")

        if any(p in file_path for p in ALLOWED_PATH_PATTERNS):
            validated.append(action)
            continue

        if _is_ui_file(file_path, entity_names):
            best = _select_best_skill(task)
            if best:
                validated.append({
                    "type": "use_skill",
                    "target": "skill",
                    "name": best,
                    "params": {"default": True},
                    "note": "UI file creation replaced by skill (semantic_skill mode)",
                })
                continue

        validated.append(action)

    plan["actions"] = validated
    plan["_semantic_validated"] = True
    return plan
