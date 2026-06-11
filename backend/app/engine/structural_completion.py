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
class SubstitutionRecord:
    """Registro de sustitución semántica.

    A diferencia de DELETE+CREATE, SubstitutionRecord preserva
    la intención: source es reemplazada por target, pero source
    NO se elimina (su lifecycle queda frozen).

    Representación canónica de 'replace X with Y'.
    """
    source_capability: str
    target_capability: str

    @property
    def pair(self) -> tuple[str, str]:
        """Backward compat: (old, new) tuple."""
        return (self.source_capability, self.target_capability)


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
    substitutions: representación canónica de reemplazos semánticos.
                   Sustitución ≠ DELETE. La source nunca se elimina.
    """
    contract_id: str
    contract_version: int
    capabilities: tuple[ResolvedCapability, ...]
    param_provenance: dict[str, str]
    confidence: float
    completion_warnings: tuple[str, ...] = ()
    layout_hints: dict[str, dict] = field(default_factory=dict)
    substitutions: tuple[SubstitutionRecord, ...] = ()

    @property
    def replace_pairs(self) -> list[tuple[str, str]]:
        """Backward compat: deriva de substitutions como lista de tuplas (old, new)."""
        return [s.pair for s in self.substitutions]

    @property
    def replace_pairs_index(self) -> dict[str, str]:
        """Backward compat: lookup O(1) new_cap → old_cap."""
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
        return any(s.target_capability == capability for s in self.substitutions)

    def is_replace_target(self, capability: str) -> bool:
        """¿Esta capability fue reemplazada por otra? (es el 'old' de un replace)"""
        return any(s.source_capability == capability for s in self.substitutions)

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
    structural_index: StructuralIndex | None = None,
) -> dict[str, str]:
    """Step A: Match action objects to capability names.

    El universo de targets posibles es structural_index ∪ contract_caps.
    Un action puede referirse a capabilities existentes en el repo
    aunque el contrato no las declare.

    1. target_hint → match directo contra universo completo
    2. OBJECT_KEYWORDS mapping (e.g., "kpi" → "kpi_row")
    3. Capability suffix (e.g., "table" → "presentation.table")
    4. Contract template keys (e.g., "Page" → "layout.page")
    5. Direct substring match

    Returns: {capability_name: action_verb}
    """
    from app.graphir.semantic_frame import _OBJECT_KEYWORDS

    matched: dict[str, str] = {}

    # Universo de targets: repo ∪ contract
    all_targets: set[str] = set(contract_caps)
    if structural_index is not None:
        all_targets |= structural_index.capability_set
    all_targets_list = sorted(all_targets)

    # Build suffix name_map for bidirectional matching
    # e.g., "bar" → "presentation.chart.bar", "kpirow" → "presentation.kpi_row"
    suffix_map: dict[str, str] = {}
    for cap_name in all_targets:
        suffix = cap_name.rsplit(".", 1)[-1]
        parts = suffix.split("_")
        pascal = "".join(p.title() for p in parts)
        suffix_map[pascal.lower()] = cap_name
        suffix_map[suffix.replace("_", "").lower()] = cap_name
        suffix_map[suffix.lower()] = cap_name

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

        # Express lane: target_hint pre-resuelto contra universo completo
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
    structural_index: StructuralIndex | None,
) -> str:
    """Step B: Determinar lifecycle action para una capability.

    Regla determinista:
      - existe en repo + verb delete  → DELETE
      - existe en repo + verb modify  → MODIFY
      - existe en repo + verb move    → MODIFY (layout-only, mismos params)
      - existe en repo + verb replace → MODIFY (old; new se añade aparte)
      - existe en repo + sin verb     → KEEP
      - no existe + verb create       → CREATE
      - no existe + sin verb          → CREATE (contract default)
      - no existe + verb move/replace → KEEP (error → clarification)
    """
    exists = structural_index is not None and structural_index.exists(capability)

    if action_verb:
        vl = action_verb.lower()
        if vl in _VERBS_DELETE:
            return DELETE if exists else KEEP
        if vl in _VERBS_MODIFY:
            return MODIFY if exists else CREATE
        if vl in _VERBS_CREATE:
            return MODIFY if exists else CREATE
        if vl in _VERBS_MOVE:
            return MODIFY if exists else KEEP
        if vl in _VERBS_REPLACE:
            return MODIFY if exists else KEEP
        if exists:
            return MODIFY  # unrecognized verb on existing → modify

    if exists:
        return KEEP
    return CREATE


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
    structural_index: StructuralIndex | None,
) -> str | None:
    """Match a single object string to a capability name.

    Reusa la lógica de _match_actions_to_capabilities sin depender
    de una action dict completa.
    """
    from app.graphir.semantic_frame import _OBJECT_KEYWORDS

    all_targets = set(contract_caps)
    if structural_index is not None:
        all_targets |= structural_index.capability_set

    obj_lower = obj.lower()
    for cap in sorted(all_targets):
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
    structural_index: StructuralIndex | None,
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
            target_cap = _match_single_object(obj, contract_caps, structural_index)
            ref_cap = _match_single_object(ref, contract_caps, structural_index)
            if target_cap and ref_cap and target_cap != ref_cap:
                hints[target_cap] = {
                    "move_after": ref_cap,
                    "scope": "layout",
                }
        elif verb in _VERBS_REPLACE and ref:
            old_cap = _match_single_object(obj, contract_caps, structural_index)
            new_cap = _match_single_object(ref, contract_caps, structural_index)
            if old_cap and new_cap and old_cap != new_cap:
                hints[new_cap] = {"replace_anchor": old_cap}
    return hints


def _extract_replace_pairs(
    semantic_resolution: SemanticResolution,
    contract_caps: list[str],
    structural_index: StructuralIndex | None,
) -> list[SubstitutionRecord]:
    """Extrae SubstitutionRecords de acciones REPLACE.

    {"verb": "replace", "object": "table", "reference": "bar chart"}
    → [SubstitutionRecord(source="presentation.table", target="presentation.chart.bar")]

    La sustitución es semántica: la source NO se elimina.
    """
    records: list[SubstitutionRecord] = []
    for action in semantic_resolution.actions:
        verb = action.get("verb", "")
        obj = action.get("object", "")
        ref = action.get("reference", "")
        if verb in _VERBS_REPLACE and obj and ref:
            old_cap = _match_single_object(obj, contract_caps, structural_index)
            new_cap = _match_single_object(ref, contract_caps, structural_index)
            if old_cap and new_cap and old_cap != new_cap:
                records.append(SubstitutionRecord(
                    source_capability=old_cap,
                    target_capability=new_cap,
                ))
    return records


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


def _ensure_graph_viability(
    resolved: list[ResolvedCapability],
    warnings: list[str],
    structural_index: StructuralIndex | None = None,
) -> None:
    """Fase 2.5b + 3C: Anchor preservation — garantizar GraphIR no vacío.

    SAFE_SKIP nunca puede eliminar TODOS los nodos del grafo.
    Si todas las capabilities resultarían en 0 nodos builder
    (solo KEEP/DELETE, sin CREATE/MODIFY), preservar el anchor.

    Condiciones para preservar:
      1. No hay CREATE/MODIFY → builder produciría 0 nodos
      2. Hay al menos un KEEP (hay intención estructural de preservar)
      3. NO todas son KEEP (si todas son KEEP → noop válido por has_resolved_keep_state)

    3C: Preferir capabilities que EXISTEN en el index para evitar scaffolds espurios.
    Solo si ninguna capability existe en disco, se recurre al scaffold (comportamiento legacy).

    Prioridad (3C): existing > layout.page > domain.* > primera capability válida.
    """
    has_builder_node = any(rc.action in (CREATE, MODIFY) for rc in resolved)
    if has_builder_node:
        return

    has_keep = any(rc.action == KEEP for rc in resolved)
    all_keep = all(rc.action == KEEP for rc in resolved)

    # No keep → all DELETE → válido, builder rechaza pero upstream maneja
    # All keep → noop válido, has_resolved_keep_state lo captura antes del builder
    if not has_keep or all_keep:
        return

    def _on_disk(cap_name: str) -> bool:
        """Check if capability exists on disk via StructuralIndex."""
        if structural_index is None:
            return True  # No index → assume exists (no contamination risk)
        return structural_index.exists(cap_name)

    # 3C: Preferir capabilities que existen on-disk
    for rc in resolved:
        if _on_disk(rc.name):
            _preserve_anchor(resolved, rc, warnings)
            return

    # Fallback legacy: sin index, preferir layout.page
    for rc in resolved:
        if rc.name == "layout.page":
            _preserve_anchor(resolved, rc, warnings)
            return

    # Fallback legacy: domain.*
    for rc in resolved:
        if rc.name.startswith("domain."):
            _preserve_anchor(resolved, rc, warnings)
            return

    # Último recurso: primera capability (puede generar scaffold espurio)
    for rc in resolved:
        _preserve_anchor(resolved, rc, warnings)
        return


def _preserve_anchor(
    resolved: list[ResolvedCapability],
    anchor: ResolvedCapability,
    warnings: list[str],
) -> None:
    """Replace anchor in resolved list with MODIFY/SAFE_COMPLETE."""
    idx = resolved.index(anchor)
    preserved = ResolvedCapability(
        name=anchor.name,
        params={},
        mode=CompletionMode.SAFE_COMPLETE,
        action=MODIFY,
        provenance=anchor.provenance,
    )
    resolved[idx] = preserved
    warnings.append(f"{anchor.name}: anchor preservation (structural viability)")


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
) -> None:
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
    """
    child_to_parent = _build_contract_composition_map(contract)
    if not child_to_parent:
        return

    resolved_map = {rc.name: i for i, rc in enumerate(resolved)}

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
    structural_index: StructuralIndex | None = None,
) -> StructuralIR:
    """Convierte SemanticResolution + ContractResolution en StructuralIR.

    Args:
        semantic_resolution: Params del lenguaje del usuario + actions.
        contract_resolution: Params del contrato (SkillIR + defaults).
        contract: SkillContract seleccionado.
        frame_dict: Dict del StructuredSemanticFrame (para hint augmentation).
        structural_index: Estado del worktree. Si es None, se asume
                          repositorio vacío (todo CREATE).

    Returns:
        StructuralIR con capabilities resueltas y ownership definitivo.

    Raises:
        ValueError: si el contrato no es compatible.
    """
    warnings: list[str] = []

    # Paso 0: validación consistencia contrato ↔ repositorio
    if structural_index is not None:
        contract_caps = _infer_capabilities_from_contract(contract)
        warnings.extend(validate_contract_repo_consistency(
            contract_caps, structural_index, contract.contract_id,
        ))

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
    # El universo de matching es el scope actual (contract caps ± augmentation)
    contract_caps_for_matching = capabilities
    action_map = _match_actions_to_capabilities(
        semantic_resolution.actions, contract_caps_for_matching, contract,
        structural_index=structural_index,
    )

    # ── Post-passes 3B: action_map enrichment ─────────────────────

    # Post-pass A: metrics/params signal → MODIFY kpi_row
    # Si el usuario mencionó métricas (revenue, growth, …) y kpi_row
    # existe en repo, añadir modify aunque el verbo cayera en page.
    if structural_index is not None and semantic_resolution.semantic_params:
        metrics_signals = {"metrics", "mentioned_metrics", "columns", "values"}
        if metrics_signals & semantic_resolution.semantic_params.keys():
            if structural_index.exists("presentation.kpi_row") and "presentation.kpi_row" not in action_map:
                action_map["presentation.kpi_row"] = "modify"

    # Post-pass B: "modify dashboard" → MODIFY presentational children.
    # Solo aplica a capabilities del CONTRATO (no a todo el index).
    # Invariante: complete_structure no introduce capabilities no pedidas
    # salvo cuando la capability confirmada es explícitamente un contenedor
    # (layout.page, dashboard). Ver AGENTS.md: "Fronteras de responsabilidad".
    if structural_index is not None:
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

    # Extraer layout_hints y substitutions de acciones semánticas
    layout_hints: dict[str, dict] = {}
    substitutions: tuple[SubstitutionRecord, ...] = ()
    if semantic_resolution.actions:
        contract_caps_for_hints = (
            _infer_capabilities_from_contract(contract)
            if semantic_resolution.actions
            else capabilities
        )
        layout_hints = _extract_layout_hints(
            semantic_resolution, contract_caps_for_hints, structural_index,
        )
        substitutions = _extract_replace_pairs(
            semantic_resolution, contract_caps_for_hints, structural_index,
        )
        # Añadir nuevas capabilities del substitution al scope
        for sub in substitutions:
            if sub.target_capability not in capabilities:
                capabilities.append(sub.target_capability)

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

        # Fase 2.5: Contract injection guard + repo-capability filter
        # Solo en modo operacional (el usuario explicitó actions).
        # Si la capability no tiene action_verb, no existe en repo,
        # y vino de contract expansion → skip (no crear nodos no pedidos)
        if action_verb is None and structural_index is not None and semantic_resolution.actions:
            if not structural_index.exists(cap):
                warnings.append(f"{cap}: contract-injected, not in repo — skipping")
                resolved.append(ResolvedCapability(
                    name=cap,
                    params={},
                    mode=CompletionMode.SAFE_SKIP,
                    action=KEEP,
                ))
                continue

        action = _resolve_action(cap, action_verb, structural_index)

        # NEW: CREATE-on-existing → instance_only (no regenerar archivo fuente)
        # El usuario pide "create X" pero X ya existe en repo.
        # La intención es instanciar X en el contenedor, no reescribir X.tsx.
        instance_only = False
        user_verb = action_map.get(cap)
        if (
            action == MODIFY
            and user_verb is not None
            and user_verb in _VERBS_CREATE
            and structural_index is not None
            and structural_index.exists(cap)
        ):
            action = KEEP
            instance_only = True
            warnings.append(
                f"{cap}: exists in repo with create intent — "
                f"instance_only (parent composition sync)"
            )

        # DELETE → no resuelven params (repo pierde la capacidad)
        if action == DELETE:
            hint = instance_hints.get(cap)
            instance_id = _resolve_delete_instance(
                cap, hint, structural_index,
            )
            resolved.append(ResolvedCapability(
                name=cap,
                params={},
                mode=CompletionMode.SAFE_SKIP,
                action=DELETE,
                instance_id=instance_id,
                instance_hint=hint,
            ))
            warnings.append(f"{cap}: marked for deletion")
            if instance_id:
                warnings.append(f"{cap}: limiting delete to instance_id={instance_id} (hint={hint})")
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

        # KEEP: preserve contract params for composition (no file generation).
        # Skip mode/missing_required checks — existing component, params are
        # composition hints only. SAFE_SKIP tells the coverage validator that
        # this capability was intentionally not completed (it already exists).
        # When _expand_composition_children promotes KEEP to INSTANCE, the
        # params carry forward for resolve_props.
        if action == KEEP:
            resolved.append(ResolvedCapability(
                name=cap,
                params=cap_params,
                mode=CompletionMode.SAFE_SKIP,
                action=KEEP,
                provenance=cap_provenance,
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
        ))

    # Fase 2.5b + 3C: Graph viability invariant — anchor preservation
    # Ensure at least one capability produces a builder node
    # 3C: pass structural_index to prefer existing caps and avoid spurious scaffolds
    _ensure_graph_viability(resolved, warnings, structural_index=structural_index)

    # 3E: Composition sync — parent page regeneration on child CREATE/DELETE.
    # After anchor preservation, promote parent to MODIFY when a child is
    # created or deleted, so the renderer regenerates the page with correct
    # imports/JSX references.
    _sync_composition_parents(resolved, contract, warnings)

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
        substitutions=tuple(substitutions),
    )



# ── Replace consistency validation ─────────────────────

def _capability_from_path(path: str) -> str | None:
    """Derive capability name from file path (simple reverse of name_map)."""
    from app.engine.apply_engine import _build_name_map, _match_file_to_capability
    name_map = _build_name_map()
    name, _ext = os.path.splitext(os.path.basename(path))
    return _match_file_to_capability(name.lower(), name_map)


def validate_replace_consistency(
    structural_ir: StructuralIR,
    fileops: list,
    structural_index: StructuralIndex | None = None,
) -> list[str]:
    """Valida consistencia entre substitutions del IR y fileops reales.

    Reglas (invariante: sustitución ≠ DELETE):
      1. Cada substitution (source→target) debe tener:
         - La source NO debe tener DELETE fileop (no se elimina)
         - Un CREATE/MODIFY fileop para target
      2. target no debe tener DELETE fileop
      3. source no debe tener CREATE fileop

    Returns lista de warnings (vacía = todo consistente).
    """
    warnings: list[str] = []
    if not structural_ir.substitutions:
        return warnings

    ops_by_target: dict[str, list[str]] = {}
    for fop in fileops:
        fop_path = fop.path if hasattr(fop, 'path') else fop.get('path', '')
        fop_action = fop.action if hasattr(fop, 'action') else fop.get('action', '')
        target = _capability_from_path(fop_path)
        if target:
            ops_by_target.setdefault(target, []).append(fop_action)

    for sub in structural_ir.substitutions:
        source = sub.source_capability
        target = sub.target_capability
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
