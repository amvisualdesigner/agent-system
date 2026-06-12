from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Literal


__all__ = [
    "IntentAction",
    "InterpretationDraft",
    "ConfirmedIntent",
    "CompiledPlan",
    "RunPhase",
    "RunState",
    "RefactorChange",
    "PendingDeletion",
    "FallbackExecutionRequest",
]


class RunPhase(str, Enum):
    """Máquina de estados explícita para el flujo interpret → confirm → apply.

    Transiciones permitidas:
        interpreting → awaiting_confirmation
        awaiting_confirmation → confirmed         (via /confirm)
        confirmed → applying                       (via /apply)
        applying → completed                       (success)
        applying → failed                          (error)
        * → cancelled
    """
    INTERPRETING = "interpreting"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    APPLYING = "applying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_VALID_TRANSITIONS: dict[RunPhase, set[RunPhase]] = {
    RunPhase.INTERPRETING: {RunPhase.AWAITING_CONFIRMATION, RunPhase.CANCELLED},
    RunPhase.AWAITING_CONFIRMATION: {RunPhase.CONFIRMED, RunPhase.CANCELLED},
    RunPhase.CONFIRMED: {RunPhase.APPLYING, RunPhase.CANCELLED},
    RunPhase.APPLYING: {RunPhase.COMPLETED, RunPhase.FAILED, RunPhase.CANCELLED},
    RunPhase.COMPLETED: set(),
    RunPhase.FAILED: set(),
    RunPhase.CANCELLED: set(),
}


def validate_transition(current: RunPhase, target: RunPhase) -> None:
    """Raise ValueError if transition from current to target is invalid.

    Self-transitions (same → same) are always allowed for idempotency.
    """
    if current == target:
        return
    allowed = _VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        allowed_str = ", ".join(p.value for p in allowed) if allowed else "none"
        raise ValueError(
            f"Cannot transition from '{current.value}' to '{target.value}'. "
            f"Allowed transitions from '{current.value}': {allowed_str}"
        )


@dataclass
class RunState:
    """Persistent state snapshot for a single run_id.

    Source of truth for interpret → confirm → apply lifecycle.
    """
    run_id: str
    phase: RunPhase = RunPhase.INTERPRETING
    interpretation_draft: dict | None = None
    confirmed_intent: dict | None = None
    compiled_plan: dict | None = None
    plan_preview: dict | None = None
    gate: dict | None = None
    error: str | None = None


# ── IntentAction (verb + target inside a single action) ─────────────


@dataclass
class IntentAction:
    verb: str
    target_capability: str
    source_capability: str | None = None
    params: dict = field(default_factory=dict)
    confidence: float = 1.0
    instance_hint: str | None = None


# ── InterpretationDraft (output of IntentInterpreter) ───────────────


@dataclass
class InterpretationDraft:
    """Propuesta de intención — salida del LLM, antes de confirmación humana."""
    interpretation_id: str
    status: str  # "ok" | "needs_clarification" | "unsupported"
    contract_id: str
    contract_version: int
    proposed_actions: list[dict]  # [{verb, target_capability, label, confidence, reason}]
    alternatives: list[dict]
    params_proposed: dict
    worktree_capabilities: list[dict]  # [{id, label, present, paths}]
    clarification_question: str | None = None
    missing_mappings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if not k.startswith("_")}


# ── ConfirmedIntent (what crosses human→machine after confirm) ──────


@dataclass
class ConfirmedIntent:
    """Intención confirmada por el usuario — única entrada para PlanCompiler."""
    contract_id: str
    contract_version: int
    actions: list[IntentAction]
    params: dict
    user_message: str
    interpretation_id: str

    def to_dict(self) -> dict:
        return {
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "actions": [asdict(a) for a in self.actions],
            "params": self.params,
            "user_message": self.user_message,
            "interpretation_id": self.interpretation_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ConfirmedIntent:
        return cls(
            contract_id=d["contract_id"],
            contract_version=d.get("contract_version", 1),
            actions=[IntentAction(**a) for a in d.get("actions", [])],
            params=d.get("params", {}),
            user_message=d.get("user_message", ""),
            interpretation_id=d.get("interpretation_id", ""),
        )


# ── CompiledPlan (output of PlanCompiler) ───────────────────────────


@dataclass
class CompiledPlan:
    """Plan semántico compilado — entrada para ApplyEngine.

    Contiene los mismos campos que el plan actual (skill_ir, semantic_frame, intents)
    pero generados de forma determinista desde ConfirmedIntent.

    Invariante: actions NO puede estar vacío si semantic_frame.actions tiene entries.
    """
    skill_ir: dict
    semantic_frame: dict
    intents: list[dict]
    contract_id: str
    actions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RefactorChange:
    """Registro de un cambio de refactorización en el pipeline de ejecución.

    change_type: "import_redirect" (sustitución de imports en archivos)
                 | "composition_sync" (promoción de parent page por hijo CREATE/DELETE)
    source: nombre de la capability origen (old)
    target: nombre de la capability destino (new)
    file_path: ruta del archivo modificado (None para composition_sync)
    reason: descripción legible del cambio
    """
    change_type: str
    source: str
    target: str
    file_path: str | None = None
    reason: str = ""


@dataclass
class FallbackExecutionRequest:
    """Adaptador estándar para fallos del pipeline de ejecución.

    No reemplaza excepciones internas — las envuelve en un formato
    estándar para la capa de API.
    """
    reason: str
    conflict_type: Literal[
        "ambiguity",
        "collision",
        "unconfirmed_deletion",
        "substitution_overflow",
        "missing_component",
        "missing_required_param",
        "delete_authority",
    ]
    level: int  # 0=info, 1=warning, 2=blocking
    details: dict = field(default_factory=dict)
    options: list[dict] = field(default_factory=list)

    def to_result(self) -> dict:
        status = "clarification_needed" if self.level >= 2 else "warning"
        return {
            "execution": {
                "status": status,
                "conflict": self.conflict_type,
                "detail": self.reason,
                "diff": None,
                "operations": [],
            },
            "context": {"fallback": True},
        }


@dataclass
class PendingDeletion:
    """Eliminación pendiente de confirmación por el usuario.

    Phase 5B: derivada de StructuralIR.capabilities en el plan_preview.
    NO contiene paths — los paths se resuelven desde StructuralIndex
    SOLO en el momento de ejecución.

    confirmed: el usuario confirmó esta eliminación (set por /apply).
    """
    capability: str
    instance_hint: str | None = None
