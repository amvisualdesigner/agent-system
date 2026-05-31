import os
import json
import logging

from fastapi import APIRouter, HTTPException
from app.config.settings import settings
from app.config.feature_flags import FEATURE_FLAGS
from app.planner.plan_generator import generate_plan
from app.planner.task_classifier import classify_task
from app.planner.skill_ir_planner import generate_skill_ir
from app.contracts.plan_request import PlanRequest
from app.contracts.skill_registry import PER_CONTRACT_THRESHOLDS
from app.utils.workspace import list_workspace_files
from app.runtime.context import build_context
from app.utils.state import write_state
from app.utils.run_id import validate_run_id

logger = logging.getLogger(__name__)


router = APIRouter()


def _dump_legacy_snapshot(run_id: str, task: str, classification, plan: dict):
    snapshot = {
        "run_id": run_id,
        "task": task,
        "classification": {
            "mode": classification.mode,
            "semantic_score": classification.semantic_score,
            "composition_score": classification.composition_score,
            "matched_patterns": classification.matched_patterns,
        },
        "plan": plan,
    }
    path = f"{settings.ARTIFACTS_DIR}/{run_id}/baseline_plan.json"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(snapshot, f, indent=2, default=str)


@router.post("/agent/plan")
def agent_plan(req: PlanRequest):

    if not req.run_id:
        raise HTTPException(status_code=400, detail="run_id is required")
    run_id = validate_run_id(req.run_id)

    context = build_context(run_id)

    workspace_files = list_workspace_files(context.workspace)

    classification = classify_task(req.task)

    if FEATURE_FLAGS["skill_ir_output"]:
        skill_ir = generate_skill_ir(req.task, classification.mode)
        ok, reason = skill_ir.should_execute(per_contract_thresholds=PER_CONTRACT_THRESHOLDS)

        if not ok:
            return {
                "run_id": run_id,
                "status": "noop",
                "reason": reason,
                "skill_ir": skill_ir.to_dict(),
                "planner_meta": {
                    "task_mode": classification.mode,
                    "semantic_score": classification.semantic_score,
                    "composition_score": classification.composition_score,
                    "matched_patterns": classification.matched_patterns,
                },
            }

        # Intent decomposition (optional — enables intent coverage checking)
        from app.graphir.intent_decomposition import decompose_task
        dec_result = decompose_task(req.task)
        intents_data = [i.to_dict() for i in dec_result.intents]

        # ── Semantic frame + confidence gate (spike) ──
        from app.graphir.semantic_frame import build_frame_from_decomposition
        from app.engine.gate import confidence_gate

        semantic_frame = build_frame_from_decomposition(req.task, dec_result)
        gate_result = confidence_gate(semantic_frame)

        # Build shared actions list from semantic frame
        plan_actions = [{"verb": a.verb, "object": a.direct_object, "confidence": a.confidence} for a in semantic_frame.actions]

        if gate_result.blocked:
            logger.warning(
                "Pipeline blocked by confidence gate: %s | missing: %s",
                gate_result.reason, gate_result.missing_info,
            )
            plan = {
                "skill_ir": skill_ir.to_dict(),
                "actions": plan_actions,
                "task": req.task,
                "intents": intents_data,
                "decomposition": {
                    "decomposition_confidence": dec_result.decomposition_confidence,
                    "detected": dec_result.detected,
                    "inferred": dec_result.inferred,
                    "unresolved": dec_result.unresolved,
                },
                "semantic_frame": {
                    "actions": plan_actions,
                    "objects": [{"type": o.type, "confidence": o.confidence} for o in semantic_frame.objects],
                    "constraints": [{"param": c.param, "value": c.value, "source": c.source} for c in semantic_frame.constraints],
                    "confidence": semantic_frame.confidence,
                    "missing_info": semantic_frame.missing_info,
                },
                "gate": {
                    "blocked": True,
                    "reason": gate_result.reason,
                },
            }
            write_state(run_id, "plan")
            return {
                "run_id": run_id,
                "status": "blocked",
                "reason": gate_result.reason,
                "plan": plan,
                "skill_ir": skill_ir.to_dict(),
                "planner_meta": {
                    "task_mode": classification.mode,
                    "semantic_score": classification.semantic_score,
                    "composition_score": classification.composition_score,
                    "matched_patterns": classification.matched_patterns,
                },
            }

        plan = {
            "skill_ir": skill_ir.to_dict(),
            "actions": plan_actions,
            "task": req.task,
            "intents": intents_data,
            "decomposition": {
                "decomposition_confidence": dec_result.decomposition_confidence,
                "detected": dec_result.detected,
                "inferred": dec_result.inferred,
                "unresolved": dec_result.unresolved,
            },
            "semantic_frame": {
                "actions": plan_actions,
                "objects": [{"type": o.type, "confidence": o.confidence} for o in semantic_frame.objects],
                "constraints": [{"param": c.param, "value": c.value, "source": c.source} for c in semantic_frame.constraints],
                "confidence": semantic_frame.confidence,
                "missing_info": semantic_frame.missing_info,
            },
        }

        write_state(run_id, "plan")

        return {
            "run_id": run_id,
            "status": "ok",
            "plan": plan,
            "skill_ir": skill_ir.to_dict(),
            "planner_meta": {
                "task_mode": classification.mode,
                "semantic_score": classification.semantic_score,
                "composition_score": classification.composition_score,
                "matched_patterns": classification.matched_patterns,
            },
        }

    # Legacy path (skill_ir_output disabled)
    plan = generate_plan(req, workspace_files, task_mode=classification.mode)

    write_state(run_id, "plan")

    _dump_legacy_snapshot(run_id, req.task, classification, {
        "original": plan,
    })

    return {
        "run_id": run_id,
        "status": "ok",
        "plan": plan,
        "planner_meta": {
            "task_mode": classification.mode,
            "semantic_score": classification.semantic_score,
            "composition_score": classification.composition_score,
            "matched_patterns": classification.matched_patterns,
        },
    }