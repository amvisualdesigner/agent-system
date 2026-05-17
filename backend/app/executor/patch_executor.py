import os
import logging

from app.policy.policy import validate_operation, safe_path
from app.contracts.operations import Action
from app.executor.skill_resolver import resolve_skill
from app.config.feature_flags import FEATURE_FLAGS

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 200_000

ALLOWED_INTENTS = {"empty", "scaffold", "full"}


def _template_for(path: str) -> str:
    ext = os.path.splitext(path)[1]
    name = os.path.basename(path)
    stem = name.rsplit(".", 1)[0] if "." in name else name
    sanitized = stem.replace("-", "_")

    if ext == ".ts":
        return f"// {name}\n\nexport const {sanitized} = () => {{\n  // TODO\n}};\n"
    if ext == ".tsx":
        return f"import React from 'react';\n\nexport const {stem}: React.FC = () => {{\n  return <div>{{{stem}}}</div>;\n}};\n"
    if ext == ".jsx":
        return f"import React from 'react';\n\nexport const {stem} = () => {{\n  return <div>{{{stem}}}</div>;\n}};\n"
    if ext == ".py":
        return f"# {name}\n\n\ndef {sanitized}():\n    pass\n"
    if ext == ".css":
        return f"/* {name} */\n"
    if ext == ".html":
        return f"<!DOCTYPE html>\n<html>\n<head>\n  <title>{name}</title>\n</head>\n<body>\n  <!-- TODO -->\n</body>\n</html>\n"
    if ext == ".json":
        return "{\n  \n}\n"
    if ext == ".md":
        return f"# {name}\n\nTODO\n"
    return ""


def _resolve_intent(intent: str, path: str) -> str:
    if FEATURE_FLAGS["freeze_scaffold"]:
        logger.warning("[DEPRECATED] scaffold/empty intent blocked by freeze_scaffold flag")
        return ""
    if intent == "empty":
        return ""
    if intent == "scaffold":
        return _template_for(path)
    if intent == "full":
        raise ValueError(
            f'intent "full" not allowed without a registered skill or external generator: {path}'
        )
    return ""


def apply_operation(op, base_repo):
    ok, reason = validate_operation(op)
    if not ok:
        return {"status": "rejected", "reason": reason, "op": op}

    target = op.get("target", "file")

    if target == "file":
        return _apply_file_op(op, base_repo)

    return _apply_skill_op(op, base_repo)


def _apply_file_op(op, base_repo):
    path = safe_path(op["path"], base_repo)

    if op["action"] == Action.create:
        intent = op.get("intent", "")
        if FEATURE_FLAGS["freeze_scaffold"] and intent in ("scaffold", "empty"):
            logger.warning("[DEPRECATED] scaffold/empty rejected by freeze_scaffold flag", extra={"path": path})
            return {"status": "rejected", "reason": "scaffold_disabled", "path": path}
        if intent in ALLOWED_INTENTS:
            content = _resolve_intent(intent, path)
        else:
            content = op.get("proposed_content") or op.get("content") or ""
        result = create_file(path, content)
        if intent == "scaffold" and result.get("status") == "created":
            result["intent"] = "scaffold"
            result["skill_used"] = False
            result["generated_by"] = "template_engine"
        return result
    elif op["action"] == Action.modify:
        content = op.get("proposed_content") or op.get("diff") or ""
        return modify_file(path, content)
    elif op["action"] == Action.delete:
        return delete_file(path)
    else:
        return {"status": "error", "reason": "unknown_operation"}


def _apply_skill_op(op, base_repo):
    name = op.get("name", "")
    params = op.get("params", {})
    ast = resolve_skill(name, params)
    if ast.get("status") == "error":
        return {
            "status": "skipped",
            "target": "skill",
            "name": name,
            "reason": f"fallback_to_file: {ast.get('reason')}",
        }
    return {
        "status": "executed",
        "target": "skill",
        "name": name,
        "ast": ast,
        "execution_mode": "skill",
        "resolution_stage": "resolved",
    }


# create = structural operation (intent to create file, executor owns content)
# modify = patch operation (requires diff, executor applies it)
def create_file(path, content):
    if len(content.encode("utf-8")) > MAX_FILE_SIZE:
        return {"status": "rejected", "reason": "file_too_large"}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    os.chmod(path, 0o644)
    return {"status": "created", "path": path}


def modify_file(path, content):
    if not os.path.isfile(path):
        return {"status": "rejected", "reason": "file_not_found", "path": path}
    if len(content.encode("utf-8")) > MAX_FILE_SIZE:
        return {"status": "rejected", "reason": "file_too_large"}
    with open(path, "w") as f:
        f.write(content)
    os.chmod(path, 0o644)
    return {"status": "modified", "path": path}


def delete_file(path):
    if not os.path.isfile(path):
        return {"status": "rejected", "reason": "file_not_found", "path": path}
    os.remove(path)
    return {"status": "deleted", "path": path}
