from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Literal


__all__ = [
    "IntentAction",
    "TargetRef",
    "AttachRef",
    "InterpretationDraft",
    "ConfirmedIntent",
    "CompiledPlan",
    "RunPhase",
    "RunState",
    "RefactorChange",
    "PendingDeletion",
    "FallbackExecutionRequest",
    "conflict_result",
    "DataSourceRef",
    "DataSchemaRef",
    "DataMappingEntry",
    "DataBinding",
    "BindingRequirement",
    "BindingProposal",
    "EvidenceItem",
    "SOURCE_KINDS",
    "BINDING_PROPOSAL_STATUSES",
]


class RunPhase(str, Enum):
    """Máquina de estados explícita para el flujo interpret → confirm → apply.

    Transiciones permitidas:
        interpreting → awaiting_confirmation
        awaiting_confirmation → confirmed          (via /confirm)
        confirmed → applying                        (via /apply)
        applying → completed                        (success)
        applying → confirmed                        (conflict resoluble, Plan intacto)
        applying → failed                           (error técnico, no resoluble)
        interpreting | awaiting_confirmation | confirmed → cancelled

    Cancelar durante `applying` NO está permitido (sub-decisión B).
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
    RunPhase.APPLYING: {RunPhase.COMPLETED, RunPhase.CONFIRMED, RunPhase.FAILED},
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
class TargetRef:
    """Referencia al destinatario físico de un AttachRef.

    capability: capability del contexto destino (p.ej. layout.page).
    instance_label: etiqueta de la instancia física (p.ej. SalesOverviewPage).
                   None => etiqueta desconocida/no especificada (ambiguo).
    """
    capability: str
    instance_label: str | None = None

    def to_dict(self) -> dict:
        d = {"capability": self.capability}
        if self.instance_label:
            d["instance_label"] = self.instance_label
        return d

    @classmethod
    def from_dict(cls, d: dict) -> TargetRef:
        if not d:
            return cls(capability="")
        return cls(
            capability=d.get("capability", ""),
            instance_label=d.get("instance_label"),
        )


@dataclass
class AttachRef:
    """Relación WHAT/WHERE: indica dónde se une un componente que se crea.

    journey: child-first — el contrato semántico lo gobierna la capability del
             hijo; el padre se expresa SOLO como destino físico vía target.
    kind: tipo de unión (por ahora "container").
    provenance: "candidate" (propuesta) | "frozen" (confirmada por el plan).
    """
    target: TargetRef
    kind: str = "container"
    provenance: str = "candidate"

    def to_dict(self) -> dict:
        return {
            "target": self.target.to_dict(),
            "kind": self.kind,
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AttachRef:
        if not d:
            return cls(target=TargetRef(capability=""), kind="container", provenance="candidate")
        return cls(
            target=TargetRef.from_dict(d.get("target", {})),
            kind=d.get("kind", "container"),
            provenance=d.get("provenance", "candidate"),
        )


@dataclass
class IntentAction:
    verb: str
    target_capability: str
    source_capability: str | None = None
    params: dict = field(default_factory=dict)
    confidence: float = 1.0
    instance_hint: str | None = None
    attach: AttachRef | None = None
    binding: "DataBinding | None" = None


# ── Data Binding (Fase 6) ───────────────────────────────────────────
#
# DataBinding = conexión de datos CONFIRMADA. Tras /agent/confirm es
# inmutable y es la única autoridad: Renderer, GraphIR y
# RepositoryValidation la consumen; nadie la redescubre ni la modifica.
#
# BindingRequirement / BindingProposal / EvidenceItem viven SOLO en la
# fase pre-confirmación. BindingProposal NO es DataBinding: son tipos
# distintos y no intercambiables.


SOURCE_KINDS = ("hook", "slice", "service", "symbol", "query")

BINDING_PROPOSAL_STATUSES = ("auto_unique", "needs_choice", "unresolved")


@dataclass(frozen=True)
class DataSourceRef:
    """Identidad estable de una fuente de datos.

    NO admite nombres libres, scores ni fuzzy matching: `kind` + `ref`
    (+ `selector`) deben resolverse de forma determinista contra el
    worktree o el registry v4.

    kind: catálogo cerrado (SOURCE_KINDS).
    ref: identificador repo-based (p.ej. "hook:useDashboardData",
         "slice:SalesOverviewPage.filters").
    selector: subpath dentro de la fuente (p.ej. "kpiData").
    """
    kind: str
    ref: str
    selector: str | None = None

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "ref": self.ref}
        if self.selector is not None:
            d["selector"] = self.selector
        return d

    @classmethod
    def from_dict(cls, d: dict | None) -> "DataSourceRef | None":
        if not d:
            return None
        return cls(
            kind=d.get("kind", ""),
            ref=d.get("ref", ""),
            selector=d.get("selector"),
        )

    def identity(self) -> tuple[str, str, str | None]:
        return (self.kind, self.ref, self.selector)


@dataclass(frozen=True)
class DataSchemaRef:
    """Referencia al esquema de una fuente — NO un snapshot de tipos.

    ref: referencia estable al esquema declarado (p.ej.
         "data_access:Page.slices" o "contract:analytics.filter").
    shape: forma mínima necesaria para validar compatibilidad
           ("scalar" | "array" | "object" | "unknown").
    fields: solo campos demostrables y relevantes para el mapping.
    """
    ref: str
    shape: str = "unknown"
    fields: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "ref": self.ref,
            "shape": self.shape,
            "fields": list(self.fields),
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "DataSchemaRef | None":
        if not d:
            return None
        fields = d.get("fields") or ()
        if isinstance(fields, str):
            fields = (fields,)
        return cls(
            ref=d.get("ref", ""),
            shape=d.get("shape", "unknown"),
            fields=tuple(fields),
        )


@dataclass(frozen=True)
class DataMappingEntry:
    """Un mapeo dato → prop del consumidor.

    Es el RESULTADO de evaluar BindingRequirement × schema de la fuente,
    nunca un input circular de la compatibilidad.

    transform: nombre de la whitelist de transforms del resolver
               (identity/items/wrap/value/label). None => "identity".
    """
    prop: str
    from_field: str
    transform: str | None = None
    required: bool = False
    default: Any | None = None

    def to_dict(self) -> dict:
        d = {
            "prop": self.prop,
            "from_field": self.from_field,
            "required": self.required,
        }
        if self.transform is not None:
            d["transform"] = self.transform
        if self.default is not None:
            d["default"] = self.default
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DataMappingEntry":
        return cls(
            prop=d.get("prop", ""),
            from_field=d.get("from_field", ""),
            transform=d.get("transform"),
            required=bool(d.get("required", False)),
            default=d.get("default"),
        )


@dataclass(frozen=True)
class DataBinding:
    """Conexión de datos CONFIRMADA — inmutable post-confirmación.

    Invariante: si el binding existe, mapping tiene ≥1 entrada y
    source.ref no está vacío.
    """
    source: DataSourceRef
    schema: DataSchemaRef | None = None
    mapping: tuple[DataMappingEntry, ...] = ()

    def __post_init__(self) -> None:
        if not self.source or not self.source.ref:
            raise ValueError("DataBinding requires a non-empty source.ref")
        if not self.mapping:
            raise ValueError("DataBinding requires at least one mapping entry")

    def to_dict(self) -> dict:
        return {
            "source": self.source.to_dict(),
            "schema": self.schema.to_dict() if self.schema else None,
            "mapping": [m.to_dict() for m in self.mapping],
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "DataBinding | None":
        """Reconstruye un DataBinding. Devuelve None si el dict está incompleto.

        Tolerancia deliberada en el frontera de transporte: un dict sin
        source o sin mapping NO es un binding, es ausencia de binding
        (binding=None). La decisión semántica "esta acción requería binding
        y no lo tiene" se toma en la validación de confirm, no aquí.
        """
        if not d:
            return None
        source = DataSourceRef.from_dict(d.get("source"))
        if source is None or not source.ref:
            return None
        mapping = tuple(
            DataMappingEntry.from_dict(m) for m in (d.get("mapping") or [])
            if isinstance(m, dict)
        )
        if not mapping:
            return None
        return cls(
            source=source,
            schema=DataSchemaRef.from_dict(d.get("schema")),
            mapping=mapping,
        )

    def mapping_for(self, prop: str) -> DataMappingEntry | None:
        for entry in self.mapping:
            if entry.prop == prop:
                return entry
        return None


@dataclass(frozen=True)
class BindingRequirement:
    """Qué necesita el consumidor ANTES de elegir fuente.

    Se construye desde evidencia ya existente (ast_template.slots[].props,
    firma real del componente, registry v4, input_schema del contrato).
    Existe para romper la circularidad: la compatibilidad se evalúa
    contra el requirement, nunca contra un mapping aún por descubrir.

    incompatibilities: evidencia en conflicto o ausente. Se REGISTRA en
    lugar de fabricar un requisito (regla "no inventar shapes"). Nunca es
    autoridad: solo explica por qué algo quedó fuera.
    """
    target_component: str
    required_props: tuple[str, ...] = ()
    expected_shapes: tuple[tuple[str, str], ...] = ()
    incompatibilities: tuple[str, ...] = ()

    def shape_for(self, prop: str) -> str | None:
        for p, shape in self.expected_shapes:
            if p == prop:
                return shape
        return None

    def to_dict(self) -> dict:
        return {
            "target_component": self.target_component,
            "required_props": list(self.required_props),
            "expected_shapes": {p: s for p, s in self.expected_shapes},
            "incompatibilities": list(self.incompatibilities),
        }


@dataclass(frozen=True)
class EvidenceItem:
    """Evidencia estructural determinista (repo-only). NUNCA autoridad.

    kind ∈ {"declared_dataslice", "existing_registry_binding",
            "existing_import", "existing_call", "contract_slot_prop"}
    """
    kind: str
    ref: str
    detail: str | None = None

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "ref": self.ref}
        if self.detail is not None:
            d["detail"] = self.detail
        return d


@dataclass(frozen=True)
class BindingProposal:
    """Propuesta de binding PRE-confirmación. NO es DataBinding.

    status:
      "auto_unique"  → 1 candidata estructuralmente válida (inequívoca)
      "needs_choice" → N>1 candidatas; requiere decisión humana
      "unresolved"   → 0 candidatas; requiere clarification_needed
    """
    status: str
    target_component: str
    source: DataSourceRef | None = None
    schema: DataSchemaRef | None = None
    mapping: tuple[DataMappingEntry, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "target_component": self.target_component,
            "source": self.source.to_dict() if self.source else None,
            "schema": self.schema.to_dict() if self.schema else None,
            "mapping": [m.to_dict() for m in self.mapping],
            "evidence": [e.to_dict() for e in self.evidence],
            "provenance": dict(self.provenance),
        }

    def to_binding(self) -> DataBinding | None:
        """Convierte una propuesta RESUELTA en DataBinding confirmable.

        Sólo es válido para status="auto_unique" o cuando el usuario ya
        eligió una candidata concreta (status="needs_choice" + selección
        explícita). Para "unresolved" devuelve None.
        """
        if self.status == "unresolved" or self.source is None or not self.mapping:
            return None
        return DataBinding(
            source=self.source,
            schema=self.schema,
            mapping=self.mapping,
        )


# ── InterpretationDraft (output of IntentInterpreter) ───────────────


@dataclass
class InterpretationDraft:
    """Propuesta de intención — salida del LLM, antes de confirmación humana."""
    interpretation_id: str
    status: str  # "ok" | "clarification_needed" | "unsupported"
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
        actions = []
        for a in d.get("actions", []):
            action = IntentAction(
                verb=a.get("verb", ""),
                target_capability=a.get("target_capability", ""),
                source_capability=a.get("source_capability"),
                params=a.get("params", {}) or {},
                confidence=a.get("confidence", 1.0),
                instance_hint=a.get("instance_hint"),
            )
            if a.get("attach"):
                action.attach = AttachRef.from_dict(a["attach"])
            # Zero-loss: el binding confirmado se reconstruye tipado.
            if a.get("binding"):
                action.binding = DataBinding.from_dict(a["binding"])
            actions.append(action)
        return cls(
            contract_id=d["contract_id"],
            contract_version=d.get("contract_version", 1),
            actions=actions,
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
        "repository_conflict",
        "fileop_provenance",
        "anchor_ambiguity",
        "target_ambiguity",
        "target_not_found",
        "invalid_confirmed_plan",
        # The workspace changed after confirm. It cannot be expressed by any
        # catalog category: it is not about WHAT, only about the physical
        # baseline. Resolution is exclusively an explicit retry.
        "concurrency",
    ]
    level: int  # 0=info, 1=warning, 2=blocking
    details: dict = field(default_factory=dict)
    options: list[dict] = field(default_factory=list)
    retryable: bool = True

    def to_result(self) -> dict:
        return conflict_result(
            self.conflict_type,
            self.reason,
            candidates=self.options,
            details=self.details,
            retryable=self.retryable,
        )


def conflict_result(
    conflict_type: str,
    detail: str,
    *,
    candidates: list[dict] | None = None,
    details: dict | None = None,
    operations: list[dict] | None = None,
    retryable: bool = True,
    run_phase: str = "confirmed",
) -> dict:
    """Canonical post-confirm conflict payload.

    Un conflicto post-confirm NO terminaliza el Run: el Confirmed Plan sigue
    siendo la única autoridad semántica y el Run vuelve a ``confirmed`` para
    permitir un retry explícito con snapshot físico fresco (D1/sub-decisión A).
    """
    execution: dict = {
        "status": "conflict",
        "stage": "apply",
        "plan_confirmed": True,
        "plan_retryable": bool(retryable),
        "run_phase": run_phase,
        "conflict": conflict_type,
        "detail": detail,
        "candidates": list(candidates or []),
        "operations": list(operations or []),
        "diff": None,
    }
    if details:
        execution["details"] = details
    return {
        "execution": execution,
        "context": {"fallback": True, "repo_snapshot": []},
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
