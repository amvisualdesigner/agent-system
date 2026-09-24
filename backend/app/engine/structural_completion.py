"""StructuralCompletionLayer — SemanticResolution + ContractResolution → StructuralIR.

Convierte dos IRs semánticos en StructuralIR: la representación estructural
final donde cada capability tiene ownership definitivo de sus params.

Pipeline:
  SemanticFrame → SemanticResolution
  SkillIR → ContractResolution
  SemanticResolution + ContractResolution → StructuralIR → GraphIR

Reglas arquitectónicas:
  1. SemanticResolution = solo lenguaje del usuario (frame constraints)
  2. ContractResolution = params de contrato validados (SkillIR + defaults)
  3. StructuralIR = ownership estructural (capability → params)
  4. Structural layer NUNCA inventa semántica de dominio
     - SAFE_COMPLETE ≠ semantic completion
     - structural defaults son solo placeholders estructurales
     - metrics/columns/KPIs/valores de dominio nunca son defaults estructurales
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.contracts.semantic_resolution import SemanticResolution
from app.contracts.contract_resolution import ContractResolution
from app.contracts.skill_registry import SkillContract, get_contract
from app.engine.structural_index import StructuralIndex
from app.intent.models import RefactorChange



class CompletionMode(Enum):
    STRICT_FAIL = "strict_fail"
    SAFE_SKIP = "safe_skip"
    SAFE_COMPLETE = "safe_complete"


# ── Action constants (lifecycle decision) ─────────────────────
CREATE = "CREATE"
MODIFY = "MODIFY"
DELETE = "DELETE"
KEEP = "KEEP"

VALID_ACTIONS = frozenset({CREATE, MODIFY, DELETE, KEEP})


class OperationError(ValueError):
    """Operation validation error — operation plan violates spec."""


def validate_operations(
    operations: list[dict],
    structural_index: StructuralIndex | None = None,
) -> list[str]:
    """Validate StructuralIR.operations against spec invariants.

    Rules (non-negotiable):
      1. All actions must be in {CREATE, MODIFY, DELETE, KEEP}
      2. No duplicate targets
      3. If structural_index provided:
         - CREATE → target must NOT exist
         - MODIFY → target must exist
         - DELETE → target must exist
         - KEEP   → target must exist

    Returns list of warnings (empty = valid).
    Raises OperationError on hard failures (rule 1, 2).
    """
    warnings: list[str] = []
    seen_targets: set[str] = set()

    for i, op in enumerate(operations):
        action = op.get("action", "")
        target = op.get("target", "")

        # Rule 1: valid action
        if action not in VALID_ACTIONS:
            raise OperationError(
                f"Operation[{i}]: invalid action '{action}'. "
                f"Must be one of {sorted(VALID_ACTIONS)}"
            )

        if not target:
            raise OperationError(f"Operation[{i}]: missing target")

        # Rule 2: no duplicate targets
        if target in seen_targets:
            raise OperationError(
                f"Duplicate target '{target}' in operations "
                f"(actions: {action})"
            )
        seen_targets.add(target)

        # Rule 3: structural_index consistency
        if structural_index is not None:
            exists = structural_index.exists(target)
            if action == CREATE and exists:
                warnings.append(
                    f"CREATE '{target}' but already exists in repo"
                )
            elif action in (MODIFY, DELETE, KEEP) and not exists:
                warnings.append(
                    f"{action} '{target}' but not found in structural_index"
                )

    return warnings


@dataclass(frozen=True)
class ResolvedCapability:
    """Capability con ownership definitivo de params y lifecycle action.

    Frozen: immutable después de creación. Ninguna capa posterior puede
    reinterpretar, redistribuir o mutar estos params o la acción.

    action: CREATE | MODIFY | DELETE | KEEP
    instance_only: True significa "la capability existe en repo, no regenerar
                    su archivo fuente; solo instanciarla en el contenedor".
                    En la práctica, action=KEEP + instance_only=True indica
                    que hubo intención CREATE sobre algo que ya existe.
    instance_id: instance_id específico para DELETE (multi-instancia).
                 None = borrar todas las instancias de la capability.
    """
    name: str
    params: dict[str, Any]
    mode: "CompletionMode"
    action: str = CREATE
    provenance: dict[str, str] = field(default_factory=dict)
    instance_only: bool = False
    instance_id: str | None = None
    instance_hint: str | None = None


@dataclass(frozen=True)
class SubstitutionOp:
    """Operación de sustitución semántica — NO es lifecycle.

    Phase 4: SubstitutionOp es un stream independiente del lifecycle.
    No tiene CREATE/MODIFY/DELETE/KEEP — solo redirección semántica.

    source: capability que se reemplaza (NO se elimina)
    target: capability que reemplaza (se crea si no existe)
    """
    source: str
    target: str

    @property
    def pair(self) -> tuple[str, str]:
        """(old, new) tuple para backward compat."""
        return (self.source, self.target)


@dataclass(frozen=True)
class SubstitutionRecord:
    """DEPRECATED — use SubstitutionOp instead.

    Mantenido temporalmente para backward compat.
    Phase 4: todo nuevo código usa SubstitutionOp.
    """
    source_capability: str
    target_capability: str

    @property
    def pair(self) -> tuple[str, str]:
        return (self.source_capability, self.target_capability)

    def to_op(self) -> SubstitutionOp:
        """Convert to canonical Phase 4 SubstitutionOp."""
        return SubstitutionOp(source=self.source_capability, target=self.target_capability)


@dataclass(frozen=True)
class StructuralIR:
    """Plan de diff estructural — operaciones sobre el repo existente.

    NO es el "estado final deseado". Es un plan de operaciones:
    cada capability tiene una acción (CREATE/MODIFY/DELETE/KEEP)
    que GraphIR debe APLICAR, no reinterpretar.

    Frozen: completamente inmutable. LangGraph-safe. Cacheable.

    operations es la vista explícita del diff plan.
    capabilities incluye todas (para auditoría/trazabilidad).
    layout_hints: metadata de layout (MOVE scope, REPLACE anchor).
    substitution_ops: stream de SubstitutionOp — independiente del lifecycle.
                      Phase 4: la sustitución NO afecta CREATE/MODIFY/DELETE/KEEP.
    substitutions: tuple[SubstitutionRecord, ...] heredado (DEPRECATED).
    composition_sync_trace: Phase 5C — traza de syncs de composición.
    """
    contract_id: str
    contract_version: int
    capabilities: tuple[ResolvedCapability, ...]
    param_provenance: dict[str, str]
    confidence: float
    completion_warnings: tuple[str, ...] = ()
    layout_hints: dict[str, dict] = field(default_factory=dict)
    substitution_ops: tuple[SubstitutionOp, ...] = ()
    substitutions: tuple[SubstitutionRecord, ...] = ()
    composition_sync_trace: tuple[RefactorChange, ...] = ()

    @property
    def replace_pairs(self) -> list[tuple[str, str]]:
        """Backward compat: deriva de substitution_ops como lista de tuplas (old, new)."""
        return [s.pair for s in self.substitution_ops] or [s.pair for s in self.substitutions]

    @property
    def replace_pairs_index(self) -> dict[str, str]:
        """Backward compat: lookup O(1) new_cap → old_cap."""
        ops_idx = {s.target: s.source for s in self.substitution_ops}
        if ops_idx:
            return ops_idx
        return {s.target_capability: s.source_capability for s in self.substitutions}

    @property
    def has_resolved_keep_state(self) -> bool:
        """True si hay capabilities Y todas decidieron KEEP.

        SAFE_SKIP NO es KEEP. Instance_only NO es KEEP (genera
        una operación INSTANCE en operations). Si todas son
        SAFE_SKIP, operations también está vacío, pero NO es
        un noop válido.
        """
        return bool(self.capabilities) and all(
            rc.action == KEEP and not rc.instance_only
            for rc in self.capabilities
        )

    def is_replacement(self, capability: str) -> bool:
        """¿Esta capability reemplaza a otra? (es el 'new' de un replace)"""
        if any(s.target == capability for s in self.substitution_ops):
            return True
        return any(s.target_capability == capability for s in self.substitutions)

    def is_replace_target(self, capability: str) -> bool:
        """¿Esta capability fue reemplazada por otra? (es el 'old' de un replace)"""
        if any(s.source == capability for s in self.substitution_ops):
            return True
        return any(s.source_capability == capability for s in self.substitutions)

    @property
    def pending_deletions(self) -> tuple[PendingDeletion, ...]:
        """Derivado de capabilities con action=DELETE.

        Phase 5B: NO contiene paths. Los paths se resuelven desde
        StructuralIndex en el momento de ejecución.
        """
        from app.intent.models import PendingDeletion
        return tuple(
            PendingDeletion(capability=rc.name, instance_hint=rc.instance_hint)
            for rc in self.capabilities
            if rc.action == DELETE
        )

    @property
    def operations(self) -> list[dict]:
        """Diff plan explícito: acciones que GraphIR debe ejecutar.

        Cada operación:
          action: CREATE | MODIFY | DELETE | KEEP | INSTANCE
          target: capability name
          payload: params (solo para CREATE/MODIFY/INSTANCE)
          instance_only: True si es una instancia (no regenerar archivo)
          instance_id: específico para DELETE multi-instancia (None = borrar todas)

        INSTANCE significa KEEP pero con intención de instanciar
        (create-on-existing). El nodo participa en composición pero
        su archivo fuente no se regenera. Equivale a
        action=KEEP + instance_only=True.
        """
        ops: list[dict] = []
        for rc in self.capabilities:
            if rc.instance_only:
                op: dict[str, Any] = {
                    "action": "INSTANCE",
                    "target": rc.name,
                    "payload": dict(rc.params),
                    "instance_only": True,
                }
                ops.append(op)
            elif rc.action not in (KEEP,):
                op: dict[str, Any] = {
                    "action": rc.action,
                    "target": rc.name,
                }
                if rc.action == DELETE:
                    if rc.instance_id:
                        op["instance_id"] = rc.instance_id
                    if rc.instance_hint:
                        op["instance_hint"] = rc.instance_hint
                elif rc.action in (CREATE, MODIFY):
                    op["payload"] = dict(rc.params)
                    if rc.action == CREATE and rc.instance_hint:
                        op["instance_hint"] = rc.instance_hint
                ops.append(op)
        return ops


STRUCTURAL_SCHEMA: dict[str, dict[str, Any]] = {
    "presentation.kpi_row": {
        "required": ["metrics"],
        "optional": ["aggregation", "format"],
        "safe_fallback": {},
        "mode": CompletionMode.SAFE_COMPLETE,
    },
    "presentation.table": {
        "required": ["columns"],
        "optional": ["table_data", "metrics", "dimensions", "top_k"],
        "safe_fallback": {},
        "mode": CompletionMode.SAFE_COMPLETE,
    },
    "presentation.timeseries": {
        "required": ["metric"],
        "optional": ["time_granularity", "group_by"],
        "safe_fallback": {},
        "mode": CompletionMode.SAFE_COMPLETE,
    },
    "presentation.chart.bar": {
        "required": ["metrics"],
        "optional": ["categories", "top_k"],
        "safe_fallback": {},
        "mode": CompletionMode.SAFE_COMPLETE,
    },
    "presentation.metric_card": {
        "required": ["metric"],
        "optional": [],
        "safe_fallback": {},
        "mode": CompletionMode.SAFE_COMPLETE,
    },
    "presentation.filter_panel": {
        "required": [],
        "optional": ["filters"],
        "safe_fallback": {},
        "mode": CompletionMode.SAFE_COMPLETE,
    },
    "presentation.embed": {
        "required": ["src"],
        "optional": ["title"],
        "safe_fallback": {},
        "mode": CompletionMode.SAFE_COMPLETE,
    },
    "domain.analytics": {
        "required": ["metrics", "dimensions"],
        "optional": [],
        "safe_fallback": {},
        "mode": CompletionMode.STRICT_FAIL,
    },
    "domain.sales": {
        "required": ["metrics", "dimensions"],
        "optional": [],
        "safe_fallback": {},
        "mode": CompletionMode.STRICT_FAIL,
    },
    "layout.page": {
        "required": [],
        "optional": ["theme"],
        "safe_fallback": {},
        "mode": CompletionMode.SAFE_COMPLETE,
    },
    "layout.grid": {
        "required": [],
        "optional": [],
        "safe_fallback": {},
        "mode": CompletionMode.SAFE_COMPLETE,
    },
    "layout.container": {
        "required": [],
        "optional": [],
        "safe_fallback": {},
        "mode": CompletionMode.SAFE_COMPLETE,
    },
    "interaction.search": {
        "required": [],
        "optional": ["placeholder"],
        "safe_fallback": {},
        "mode": CompletionMode.SAFE_COMPLETE,
    },
    "interaction.form": {
        "required": [],
        "optional": ["fields"],
        "safe_fallback": {},
        "mode": CompletionMode.SAFE_COMPLETE,
    },
    "data.export": {
        "required": [],
        "optional": ["format"],
        "safe_fallback": {},
        "mode": CompletionMode.SAFE_COMPLETE,
    },
    "data.drilldown": {
        "required": [],
        "optional": ["target", "label"],
        "safe_fallback": {},
        "mode": CompletionMode.SAFE_COMPLETE,
    },
}


def _infer_capabilities_from_contract(contract: SkillContract) -> list[str]:
    """Extrae capabilities del ast_template.capabilities del contrato.

    Esta es la fuente PRIMARIA de nodos. No usa intents ni keyword decomposition.
    """
    caps = contract.ast_template.get("capabilities", {})
    return sorted(set(caps.values()))


def _augment_capabilities(
    base_capabilities: list[str],
    semantic_resolution: SemanticResolution,
    contract_resolution: ContractResolution,
    frame_dict: dict | None,
) -> list[str]:
    """Soft-add capabilities basadas en el semantic_frame (objetos, no léxico).

    NO usa intents. NO usa keyword decomposition.
    Usa SOLO el frame validado a nivel de objetos.
    """
    if frame_dict is None:
        return base_capabilities

    augmented = set(base_capabilities)
    objects = frame_dict.get("objects", [])

    has_table_object = any(o.get("type") == "table" for o in objects)
    has_table_signal = (
        "columns" in semantic_resolution.semantic_params
        or "mentioned_metrics" in semantic_resolution.semantic_params
    )
    if has_table_object and has_table_signal:
        augmented.add("presentation.table")

    return sorted(augmented)


def _resolve_completion_mode(capability: str, confidence: float) -> CompletionMode:
    """Determina el modo de completion para una capability."""
    schema = STRUCTURAL_SCHEMA.get(capability)
    if schema is None:
        return CompletionMode.SAFE_SKIP

    mode = schema.get("mode", CompletionMode.SAFE_COMPLETE)

    # domain.* con confianza baja → SAFE_SKIP
    if capability.startswith("domain.") and confidence < 0.6:
        return CompletionMode.SAFE_SKIP

    return mode


# ── Action verb sets (Step B: lifecycle resolution) ───────
_VERBS_MODIFY = frozenset({
    "modify", "update", "change", "edit",
    "override", "overwrite", "use instead",
})
_VERBS_DELETE = frozenset({"remove", "delete", "destroy"})
_VERBS_CREATE = frozenset({
    "add", "create", "show", "build", "generate",
    "compose", "design", "include", "insert",
})
_VERBS_MOVE = frozenset({"move", "reorder", "relocate"})
_VERBS_REPLACE = frozenset({"replace", "swap", "substitute", "transform", "convert", "migrate", "morph"})


def validate_contract_repo_consistency(
    contract_caps: list[str],
    structural_index: StructuralIndex | None = None,
    contract_id: str = "",
) -> list[str]:
    """Validación bidireccional contrato ↔ repositorio.

    Rules:
      1. Every capability in contract must have a node in structural_index
      2. Every node in structural_index must map to a contract capability

    Returns list of warnings (vacía si todo está consistente).
    No bloquea — solo advierte.
    """
    warnings: list[str] = []

    if structural_index is None:
        return warnings

    contract_set = set(contract_caps)
    # Rule 1: contract → repo
    for cap in contract_set:
        if not structural_index.exists(cap):
            warnings.append(
                f"[contract-repo] {contract_id}: capability '{cap}' "
                f"declared in contract but not found in repo"
            )

    # Rule 2: repo → contract
    for cap in structural_index:
        if cap not in contract_set:
            warnings.append(
                f"[contract-repo] {contract_id}: capability '{cap}' "
                f"exists in repo but not declared in contract"
            )

    return warnings


def _match_actions_to_capabilities(
    actions: list[dict],
    contract_caps: list[str],
    contract: SkillContract | None = None,
) -> dict[str, str]:
    """Step A: Match action objects to capability names.

    Phase 3: contract-first closed-world. Solo contract_caps.
    No repo expansion. StructuralIndex es post-hoc.

    1. target_hint → match directo
    2. OBJECT_KEYWORDS mapping (e.g., "kpi" → "kpi_row")
    3. Capability suffix (e.g., "table" → "presentation.table")
    4. Contract template keys (e.g., "Page" → "layout.page")
    5. Direct substring match

    Returns: {capability_name: action_verb}
    """
    from app.graphir.semantic_frame import _OBJECT_KEYWORDS

    matched: dict[str, str] = {}

    # Universo de targets: solo contract caps (Phase 3 closed-world)
    all_targets_list = sorted(set(contract_caps))

    # Build reverse lookup from contract template keys (e.g., "Page" → "layout.page")
    template_reverse: dict[str, str] = {}
    if contract is not None:
        for key, val in contract.ast_template.get("capabilities", {}).items():
            template_reverse[key.lower()] = val

    # Semantic aliases: objects that don't directly map via OBJECT_KEYWORDS
    # but are clearly the same concept (e.g., "dashboard" → "page")
    SEMANTIC_ALIASES: dict[str, str] = {
        "dashboard": "page",
    }

    for action in actions:
        verb = action.get("verb", "")
        obj = action.get("object", "")
        target_hint = action.get("target_hint", "")

        if not verb:
            continue

        # Express lane: target_hint pre-resuelto contra contract caps
        if target_hint and target_hint in all_targets:
            matched[target_hint] = verb
            continue

        if not obj:
            continue

        obj_lower = obj.lower()
        aliases = {obj_lower, SEMANTIC_ALIASES.get(obj_lower, "")} - {""}

        for cap in all_targets_list:
            if cap in matched:
                continue
            cap_lower = cap.lower()

            # 1. OBJECT_KEYWORDS → short type in capability name
            obj_type = _OBJECT_KEYWORDS.get(obj_lower)
            if obj_type and obj_type in cap_lower:
                matched[cap] = verb
                continue

            # 2. Suffix map match: suffix key in object or object in suffix key
            # e.g., "bar" (suffix of presentation.chart.bar) in "barchart" → match
            suffix_key = cap.rsplit(".", 1)[-1].lower()
            # Check: suffix is in object, or object is in suffix
            if suffix_key in obj_lower or obj_lower in suffix_key or obj_lower == suffix_key:
                matched[cap] = verb
                continue

            # 3. Direct substring: object or alias in capability name
            if any(a in cap_lower for a in aliases):
                matched[cap] = verb
                continue

            # 4. Contract template key matches (e.g., "page" → "layout.page")
            if any(a in template_reverse and template_reverse[a] == cap for a in aliases):
                matched[cap] = verb
                continue

    return matched


def _resolve_action(
    capability: str,
    action_verb: str | None,
) -> str:
    """Step B: lifecycle action puramente semántica.

    Phase 3: contract-first closed-world. Sin structural_index.
    La acción se deriva SOLO del verbo de intención del usuario.
    ApplyEngine reconcilia después con la realidad del repo.

    DELETE  → DELETE
    MODIFY  → MODIFY
    CREATE  → CREATE
    MOVE    → MODIFY
    REPLACE → KEEP  (solo SubstitutionOp, sin lifecycle)
    None    → KEEP  (sin intención → preservar)
    """
    if action_verb:
        vl = action_verb.lower()
        if vl in _VERBS_DELETE:
            return DELETE
        if vl in _VERBS_MODIFY:
            return MODIFY
        if vl in _VERBS_CREATE:
            return CREATE
        if vl in _VERBS_MOVE:
            return MODIFY

    return KEEP


def _resolve_delete_instance(
    capability: str,
    instance_hint: str | None,
    structural_index: StructuralIndex | None,
) -> str | None:
    """Resolve instance_id for DELETE.

    MUST follow DELETE RESOLUTION CONTRACT v1
    (backend/app/engine/delete_resolution_contract.py).

    Structural paths are abstract (short_name:N) and can't distinguish
    files within the same capability (e.g. Timeseries.tsx vs LineChart.tsx
    both map to presentation.timeseries). Actual file-level resolution
    happens in the apply engine's delete loop.

    Returns instance_id or None (single instance, no filtering needed).
    Raises AmbiguousStructuralTargetError when resolution is impossible.
    """
    if structural_index is None:
        return None

    instances = structural_index.get_instances(capability)
    if not instances:
        return None  # capability not in index
    if len(instances) <= 1:
        return None  # single instance → no ambiguity

    # Multiple instances with abstract paths — defer to apply engine
    # which has actual filesystem paths. Signal via the hint.
    if not instance_hint:
        short = capability.rsplit(".", 1)[-1]
        from app.engine.errors import AmbiguousStructuralTargetError
        raise AmbiguousStructuralTargetError(
            f"There are {len(instances)} {short} components. "
            f"Please specify which one to remove "
            f"(e.g. \"remove the line chart\" or \"remove the {short} chart\")."
        )

    return None  # defer to apply engine with hint


# ── Layout hint extraction (MOVE scope, REPLACE anchor) ───────

def _match_single_object(
    obj: str,
    contract_caps: list[str],
) -> str | None:
    """Match a single object string to a capability name.

    Phase 3: contract-first closed-world. Solo contract_caps.
    """
    from app.graphir.semantic_frame import _OBJECT_KEYWORDS

    obj_lower = obj.lower()
    for cap in sorted(set(contract_caps)):
        cap_lower = cap.lower()
        suffix = cap.rsplit(".", 1)[-1].lower()
        if suffix in obj_lower or obj_lower in suffix or obj_lower == suffix:
            return cap
        obj_type = _OBJECT_KEYWORDS.get(obj_lower)
        if obj_type and obj_type in cap_lower:
            return cap
    return None


def _extract_layout_hints(
    semantic_resolution: SemanticResolution,
    contract_caps: list[str],
) -> dict[str, dict]:
    """Convierte acciones MOVE/REPLACE en layout_hints para StructuralIR.

    MOVE (scope="layout"):
      {"verb": "move", "object": "kpi", "reference": "table"}
      → {"presentation.kpi_row": {"move_after": "presentation.table", "scope": "layout"}}

    REPLACE:
      {"verb": "replace", "object": "table", "reference": "bar chart"}
      → {"presentation.chart.bar": {"replace_anchor": "presentation.table"}}
    """
    hints: dict[str, dict] = {}
    for action in semantic_resolution.actions:
        verb = action.get("verb", "")
        obj = action.get("object", "")
        ref = action.get("reference", "")
        if not verb or not obj:
            continue
        if verb in _VERBS_MOVE and ref:
            target_cap = _match_single_object(obj, contract_caps)
            ref_cap = _match_single_object(ref, contract_caps)
            if target_cap and ref_cap and target_cap != ref_cap:
                hints[target_cap] = {
                    "move_after": ref_cap,
                    "scope": "layout",
                }
        elif verb in _VERBS_REPLACE and ref:
            old_cap = _match_single_object(obj, contract_caps)
            new_cap = _match_single_object(ref, contract_caps)
            if old_cap and new_cap and old_cap != new_cap:
                hints[new_cap] = {"replace_anchor": old_cap}
    return hints


def _extract_substitution_ops(
    semantic_resolution: SemanticResolution,
    contract_caps: list[str],
) -> list[SubstitutionOp]:
    """Extrae SubstitutionOps de acciones REPLACE.

    Phase 4: stream independiente del lifecycle.
    NO toca _resolve_action, NO produce CREATE/MODIFY/DELETE/KEEP.

    {"verb": "replace", "object": "table", "reference": "bar chart"}
    → [SubstitutionOp(source="presentation.table", target="presentation.chart.bar")]

    La sustitución es semántica: la source NO se elimina.
    """
    ops: list[SubstitutionOp] = []
    for action in semantic_resolution.actions:
        verb = action.get("verb", "")
        obj = action.get("object", "")
        ref = action.get("reference", "")
        if verb in _VERBS_REPLACE and obj and ref:
            old_cap = _match_single_object(obj, contract_caps)
            new_cap = _match_single_object(ref, contract_caps)
            if old_cap and new_cap and old_cap != new_cap:
                ops.append(SubstitutionOp(source=old_cap, target=new_cap))
    return ops


def _resolve_field(
    field: str,
    capability: str,
    schema: dict,
    semantic_params: dict,
    contract_params: dict,
    slot_map: dict[str, str],
    contract: SkillContract,
) -> tuple[Any, str | None]:
    """Resolve a single capability field from available sources.

    Priority (strict):
      1. semantic_params (language — user wins)
      2. slot_map → contract_params (contract adaptation)
      3. contract input_schema default (structural safety)

    Returns:
        (value, provenance_source) or (None, None) if not found.
    """
    # Source 1: semantic (language)
    if field in semantic_params:
        return semantic_params[field], "semantic"

    # Source 2: slot mapping → contract param
    if field in slot_map:
        contract_param = slot_map[field]
        if contract_param in contract_params:
            return contract_params[contract_param], f"contract.{contract_param}"

    # Source 3: contract input_schema default (structural safety net)
    properties = contract.input_schema.get("properties", {})
    for cparam, prop in properties.items():
        if "default" in prop:
            # Check if this contract param maps to our field via slot_map
            if field in slot_map and slot_map[field] == cparam:
                return prop["default"], "contract_default"
            # Direct name match
            if field == cparam:
                return prop["default"], "contract_default"

    return None, None


def _build_contract_composition_map(contract: SkillContract) -> dict[str, str]:
    """Build {child_capability: parent_capability} from contract ast_template.

    The parent is the Page-type capability (layout.page). Children are the
    slot capabilities. Only contracts with explicit Page + slots get a map.

    Example:
        dashboard.sales_overview:
        {"presentation.kpi_row": "layout.page",
         "presentation.timeseries": "layout.page"}

    Returns:
        Empty dict if no composition hierarchy (no Page capability or no slots).
    """
    caps = contract.ast_template.get("capabilities", {})
    slots = contract.ast_template.get("slots", [])

    if not caps or not slots:
        return {}

    # Identify page parent: "Page" entry or layout.* capability
    parent = None
    for name, cap_id in caps.items():
        if name == "Page" or cap_id.startswith("layout."):
            parent = cap_id
            break

    if not parent:
        return {}

    child_to_parent: dict[str, str] = {}
    for slot in slots:
        stype = slot.get("type", "")
        if stype in caps:
            child_to_parent[caps[stype]] = parent

    return child_to_parent


def _sync_composition_parents(
    resolved: list[ResolvedCapability],
    contract: SkillContract,
    warnings: list[str],
) -> list[RefactorChange]:
    """Post-pass C: When a child capability is CREATE/DELETE/instance_only,
    ensure parent page is MODIFY.

    This ensures the parent page is regenerated by the renderer with correct
    composition (imports/JSX references) after children are added, removed,
    or instantiated.

    Rules:
      1. CREATE/DELETE/instance_only on a child → parent page MODIFY
      2. MODIFY on a child → NO parent change (no composition change needed)
      3. Parent already CREATE/MODIFY → idempotent (no double promotion)
      4. No composition map (contract without Page + slots) → no-op
      5. instance_only means the child exists in repo and was requested
         as CREATE → parent needs reference, child file stays untouched.

    Returns list of RefactorChange for composition_sync trace.
    """
    from app.intent.models import RefactorChange

    child_to_parent = _build_contract_composition_map(contract)
    if not child_to_parent:
        return []

    resolved_map = {rc.name: i for i, rc in enumerate(resolved)}
    trace: list[RefactorChange] = []

    for rc in resolved:
        is_composition_trigger = (
            rc.action in (CREATE, DELETE) or rc.instance_only
        )
        if not is_composition_trigger:
            continue
        if rc.name not in child_to_parent:
            continue

        parent_cap = child_to_parent[rc.name]
        if parent_cap not in resolved_map:
            continue

        parent_idx = resolved_map[parent_cap]
        parent_rc = resolved[parent_idx]

        # Already being created/modified — composition already handled
        if parent_rc.action in (CREATE, MODIFY):
            continue

        # Promote parent from KEEP to MODIFY/SAFE_COMPLETE
        trigger = "instance_only" if rc.instance_only else rc.action
        modified = ResolvedCapability(
            name=parent_rc.name,
            params=parent_rc.params,
            mode=CompletionMode.SAFE_COMPLETE,
            action=MODIFY,
            provenance=parent_rc.provenance,
        )
        resolved[parent_idx] = modified
        warnings.append(
            f"{parent_cap}: composition sync ({trigger} {rc.name})"
        )
        trace.append(RefactorChange(
            change_type="composition_sync",
            source=rc.name,
            target=parent_cap,
            file_path=None,
            reason=f"{parent_cap}: composition sync ({trigger} {rc.name})",
        ))

    return trace


def _expand_composition_children(
    resolved: list[ResolvedCapability],
    contract: SkillContract,
    warnings: list[str],
) -> None:
    """Post-pass C2: When parent Page is MODIFY/CREATE, promote KEEP slot
    children to INSTANCE so they appear in GraphIR composition.

    The GraphIR builder (build_from_structural) only includes operations
    with action CREATE, MODIFY, or instance_only=True. KEEP children
    are skipped, causing them to be absent from regenerated parent pages.

    This function ensures that when a parent is being regenerated, all its
    slot children are present in the graph as instance_only nodes.

    Rules:
      1. Parent action is MODIFY or CREATE -> all KEEP children -> INSTANCE
      2. Children already CREATE/DELETE/MODIFY -> no change
      3. No composition map -> no-op
    """
    child_to_parent = _build_contract_composition_map(contract)
    if not child_to_parent:
        return

    parent_to_children: dict[str, list[str]] = {}
    for child, parent in child_to_parent.items():
        parent_to_children.setdefault(parent, []).append(child)

    resolved_map = {rc.name: i for i, rc in enumerate(resolved)}

    for parent_name, children in parent_to_children.items():
        parent_idx = resolved_map.get(parent_name)
        if parent_idx is None:
            continue
        parent_rc = resolved[parent_idx]

        if parent_rc.action not in (CREATE, MODIFY):
            continue

        for child_name in children:
            child_idx = resolved_map.get(child_name)
            if child_idx is None:
                continue
            child_rc = resolved[child_idx]

            if child_rc.action != KEEP:
                continue

            promoted = ResolvedCapability(
                name=child_rc.name,
                params=child_rc.params,
                mode=child_rc.mode,
                action=KEEP,
                provenance=child_rc.provenance,
                instance_only=True,
            )
            resolved[child_idx] = promoted
            warnings.append(
                f"{child_name}: composition child expansion (parent {parent_name} {parent_rc.action})"
            )


def complete_structure(
    semantic_resolution: SemanticResolution,
    contract_resolution: ContractResolution,
    contract: SkillContract,
    frame_dict: dict | None = None,
) -> StructuralIR:
    """Convierte SemanticResolution + ContractResolution en StructuralIR.

    Phase 3: contract-first closed-world. Sin repo state.
    Lifecycle decisions solo desde IntentAction.

    Args:
        semantic_resolution: Params del lenguaje del usuario + actions.
        contract_resolution: Params del contrato (SkillIR + defaults).
        contract: SkillContract seleccionado.
        frame_dict: Dict del StructuredSemanticFrame (para hint augmentation).

    Returns:
        StructuralIR con capabilities resueltas y ownership definitivo.

    Raises:
        ValueError: si el contrato no es compatible.
    """
    warnings: list[str] = []

    # Paso 1: scope bootstrap según modo
    # Siempre se parte de las capabilities del contrato como base.
    # Operational mode: contract + action_map definen scope final.
    # Declarative mode: contract + augmentation definen scope final.
    capabilities = _infer_capabilities_from_contract(contract)
    if not semantic_resolution.actions:
        capabilities = _augment_capabilities(
            capabilities, semantic_resolution, contract_resolution, frame_dict,
        )

    # Paso 2b: Step A — match actions from semantic layer to capabilities
    # Phase 3: contract-first closed-world. Solo contract caps.
    contract_caps_for_matching = capabilities
    action_map = _match_actions_to_capabilities(
        semantic_resolution.actions, contract_caps_for_matching, contract,
    )

    # ── Post-passes 3B: action_map enrichment ─────────────────────
    # Phase 3: contract-first closed-world. Sin guards de structural_index.

    # Post-pass A: metrics/params signal → MODIFY kpi_row
    # Phase 3: solo si hay acciones semánticas (sin intent → no lifecycle).
    if semantic_resolution.actions and semantic_resolution.semantic_params:
        metrics_signals = {"metrics", "mentioned_metrics", "columns", "values"}
        if metrics_signals & semantic_resolution.semantic_params.keys():
            if "presentation.kpi_row" not in action_map:
                action_map["presentation.kpi_row"] = "modify"

    # Post-pass B: "modify dashboard" → MODIFY presentational children.
    # Solo aplica a capabilities del CONTRATO.
    for action in semantic_resolution.actions:
        verb = action.get("verb", "").lower()
        obj = action.get("object", "").lower()
        if verb in _VERBS_MODIFY and obj in ("dashboard",):
            for cap in contract_caps_for_matching:
                if cap.startswith("presentation.") and cap not in action_map:
                    action_map[cap] = "modify"

    # Ampliar scope con targets de acciones que no están en capabilities
    # (tanto repo-only como CREATE targets que no existen en repo aún)
    for target in set(action_map) - set(capabilities):
        capabilities.append(target)

    # Extraer layout_hints y substitution_ops de acciones semánticas
    layout_hints: dict[str, dict] = {}
    substitution_ops: tuple[SubstitutionOp, ...] = ()
    if semantic_resolution.actions:
        contract_caps_for_hints = (
            _infer_capabilities_from_contract(contract)
            if semantic_resolution.actions
            else capabilities
        )
        layout_hints = _extract_layout_hints(
            semantic_resolution, contract_caps_for_hints,
        )
        substitution_ops = tuple(_extract_substitution_ops(
            semantic_resolution, contract_caps_for_hints,
        ))
    # Paso 3: resolver cada capability con lifecycle action (Step B)
    resolved: list[ResolvedCapability] = []

    # Build instance_hints map from semantic actions for multi-instance DELETE
    instance_hints: dict[str, str] = {}
    for action in semantic_resolution.actions:
        hint = action.get("instance_hint")
        if hint:
            obj = action.get("object") or action.get("direct_object") or ""
            # Find the capability that matches this action's object
            for cap in capabilities:
                if cap.endswith(obj):
                    instance_hints[cap] = hint
                    break

    for cap in capabilities:
        action_verb = action_map.get(cap)

        action = _resolve_action(cap, action_verb)
        instance_only = False

        # DELETE → no resuelven params (repo pierde la capacidad)
        # Phase 3: instance_id resolution moves to ApplyEngine (Step 6).
        if action == DELETE:
            hint = instance_hints.get(cap)
            resolved.append(ResolvedCapability(
                name=cap,
                params={},
                mode=CompletionMode.SAFE_SKIP,
                action=DELETE,
                instance_id=None,
                instance_hint=hint,
            ))
            warnings.append(f"{cap}: marked for deletion")
            if hint:
                warnings.append(f"{cap}: instance_hint={hint}")
            continue

        schema = STRUCTURAL_SCHEMA.get(cap)
        if schema is None:
            warnings.append(f"{cap}: no structural schema — skipping")
            continue

        slot_map = contract.capability_param_map.get(cap, {})

        # Build params from available sources
        cap_params: dict[str, Any] = {}
        cap_provenance: dict[str, str] = {}

        for field in schema.get("required", []) + schema.get("optional", []):
            value, source = _resolve_field(
                field=field,
                capability=cap,
                schema=schema,
                semantic_params=semantic_resolution.semantic_params,
                contract_params=contract_resolution.contract_params,
                slot_map=slot_map,
                contract=contract,
            )
            if source is not None:
                cap_params[field] = value
                cap_provenance[field] = source

        missing_required = [r for r in schema.get("required", []) if r not in cap_params]

        # KEEP: Phase 3 no-op. No params, no inference.
        if action == KEEP:
            resolved.append(ResolvedCapability(
                name=cap,
                params={},
                mode=CompletionMode.SAFE_SKIP,
                action=KEEP,
                instance_only=instance_only,
            ))
            continue

        # Runtime mode override
        mode = _resolve_completion_mode(cap, contract_resolution.confidence)
        if mode == CompletionMode.STRICT_FAIL and missing_required:
            mode = CompletionMode.SAFE_SKIP

        # SAFE_SKIP → omitir capability
        if mode == CompletionMode.SAFE_SKIP:
            resolved.append(ResolvedCapability(
                name=cap,
                params={},
                mode=mode,
                action=action,
            ))
            warnings.append(f"{cap}: skipped")
            continue

        # SAFE_COMPLETE con required faltantes → SAFE_SKIP
        if mode == CompletionMode.SAFE_COMPLETE and missing_required:
            resolved.append(ResolvedCapability(
                name=cap,
                params={},
                mode=CompletionMode.SAFE_SKIP,
                action=action,
            ))
            warnings.append(
                f"{cap}: missing required fields {missing_required} "
                f"and no safe_fallback — skipped"
            )
            continue

        resolved.append(ResolvedCapability(
            name=cap,
            params=cap_params,
            mode=mode,
            action=action,
            provenance=cap_provenance,
            instance_only=instance_only,
            instance_hint=instance_hints.get(cap) if action == CREATE else None,
        ))

    # F1/C3: no anchor-preservation pass. A KEEP/DELETE operation never invents
    # a MODIFY just to give the builder a node: pure KEEP/DELETE plans are
    # delete-only materializations (apply_engine skips GraphIR for them).
    # 3E: Composition sync — parent page regeneration on child CREATE/DELETE.
    # Promotes a parent to MODIFY only when a composition child (contract slot)
    # is created or deleted, so the renderer regenerates the page with correct
    # imports/JSX references.
    composition_sync_trace = _sync_composition_parents(resolved, contract, warnings)

    # 3E (C2): Composition child expansion — when parent Page is MODIFY/CREATE,
    # promote KEEP slot children to INSTANCE so they appear in GraphIR composition.
    # Without this, the GraphIR builder skips KEEP children and they are absent
    # from regenerated parent pages.
    _expand_composition_children(resolved, contract, warnings)

    # Aggregate provenance from all capabilities
    all_provenance: dict[str, str] = {}
    for rc in resolved:
        for k, v in rc.provenance.items():
            if k not in all_provenance:
                all_provenance[k] = v

    return StructuralIR(
        contract_id=contract.contract_id,
        contract_version=contract.version,
        capabilities=tuple(resolved),
        param_provenance=all_provenance,
        confidence=contract_resolution.confidence,
        completion_warnings=tuple(warnings),
        layout_hints=layout_hints,
        substitution_ops=substitution_ops,
        substitutions=(),
        composition_sync_trace=tuple(composition_sync_trace),
    )



# ── Replace consistency validation ─────────────────────

def _capability_from_path(path: str) -> str | None:
    """Derive capability name from file path (simple reverse of name_map)."""
    from app.engine.apply_engine import _build_name_map, _match_file_to_capability
    name_map = _build_name_map()
    name, _ext = os.path.splitext(os.path.basename(path))
    return _match_file_to_capability(name.lower(), name_map)


def _validate_substitution_ops_consistency(
    structural_ir: StructuralIR,
    fileops: list,
    structural_index: StructuralIndex | None = None,
) -> list[str]:
    """Valida consistencia entre substitution_ops del IR y fileops reales.

    Reglas (invariante: sustitución ≠ DELETE):
      1. Cada substitution (source→target) debe tener:
         - La source NO debe tener DELETE fileop (no se elimina)
         - Un CREATE/MODIFY fileop para target
      2. target no debe tener DELETE fileop
      3. source no debe tener CREATE fileop

    Returns lista de warnings (vacía = todo consistente).
    """
    warnings: list[str] = []
    substitutions = list(structural_ir.substitution_ops)
    # Fallback a deprecated substitutions si no hay substitution_ops
    if not substitutions:
        substitutions = [s.to_op() for s in structural_ir.substitutions]
    if not substitutions:
        return warnings

    ops_by_target: dict[str, list[str]] = {}
    for fop in fileops:
        fop_path = fop.path if hasattr(fop, 'path') else fop.get('path', '')
        fop_action = fop.action if hasattr(fop, 'action') else fop.get('action', '')
        target = _capability_from_path(fop_path)
        if target:
            ops_by_target.setdefault(target, []).append(fop_action)

    for sub in substitutions:
        source = sub.source
        target = sub.target
        source_ops = ops_by_target.get(source, [])
        source_exists = structural_index is not None and structural_index.exists(source)
        # Invariante: source NO se elimina
        if "delete" in source_ops and source_exists:
            warnings.append(
                f"substitution ({source}→{target}): "
                f"source '{source}' has DELETE fileop — "
                f"substitution must not delete source"
            )
        target_ops = ops_by_target.get(target, [])
        if "create" not in target_ops and "modify" not in target_ops:
            warnings.append(
                f"substitution ({source}→{target}): "
                f"target '{target}' has no CREATE/MODIFY fileop"
            )
        if "delete" in target_ops:
            warnings.append(
                f"substitution ({source}→{target}): "
                f"target '{target}' has DELETE fileop — inconsistent"
            )
        if "create" in source_ops:
            warnings.append(
                f"substitution ({source}→{target}): "
                f"source '{source}' has CREATE fileop — should be no-op"
            )

    return warnings
