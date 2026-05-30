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
from app.graphir.intent import Intent, IntentPlan, make_intent_id


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
    """
    name: str
    params: dict[str, Any]
    mode: "CompletionMode"
    action: str = CREATE
    provenance: dict[str, str] = field(default_factory=dict)


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
    replace_pairs: preserva intención semántica de reemplazo.
    replace_pairs_index: lookup O(1) new_cap → old_cap.
    """
    contract_id: str
    contract_version: int
    capabilities: tuple[ResolvedCapability, ...]
    param_provenance: dict[str, str]
    confidence: float
    completion_warnings: tuple[str, ...] = ()
    layout_hints: dict[str, dict] = field(default_factory=dict)
    replace_pairs: list[tuple[str, str]] = field(default_factory=list)
    replace_pairs_index: dict[str, str] = field(default_factory=dict)

    @property
    def has_resolved_keep_state(self) -> bool:
        """True si hay capabilities Y todas decidieron KEEP.

        SAFE_SKIP NO es KEEP. Si todas son SAFE_SKIP, operations
        también está vacío, pero NO es un noop válido.
        """
        return bool(self.capabilities) and all(
            rc.action == KEEP for rc in self.capabilities
        )

    def is_replacement(self, capability: str) -> bool:
        """¿Esta capability reemplaza a otra? (es el 'new' de un replace)"""
        return capability in self.replace_pairs_index

    def is_replace_target(self, capability: str) -> bool:
        """¿Esta capability fue reemplazada por otra? (es el 'old' de un replace)"""
        return any(old == capability for old, _ in self.replace_pairs)

    @property
    def operations(self) -> list[dict]:
        """Diff plan explícito: acciones que GraphIR debe ejecutar.

        Cada operación:
          action: CREATE | MODIFY | DELETE | KEEP
          target: capability name
          payload: params (solo para CREATE/MODIFY)
        """
        ops: list[dict] = []
        for rc in self.capabilities:
            if rc.action in (KEEP,):
                continue
            op: dict[str, Any] = {
                "action": rc.action,
                "target": rc.name,
            }
            if rc.action in (CREATE, MODIFY):
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
_VERBS_REPLACE = frozenset({"replace", "swap", "substitute"})


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
) -> list[tuple[str, str]]:
    """Extrae replace_pairs de acciones REPLACE.

    {"verb": "replace", "object": "table", "reference": "bar chart"}
    → [("presentation.table", "presentation.chart.bar")]

    El par semántico se preserva en StructuralIR. El renderer traduce
    a DELETE+CREATE fileops, pero el IR mantiene la intención.
    """
    pairs: list[tuple[str, str]] = []
    for action in semantic_resolution.actions:
        verb = action.get("verb", "")
        obj = action.get("object", "")
        ref = action.get("reference", "")
        if verb in _VERBS_REPLACE and obj and ref:
            old_cap = _match_single_object(obj, contract_caps, structural_index)
            new_cap = _match_single_object(ref, contract_caps, structural_index)
            if old_cap and new_cap and old_cap != new_cap:
                pairs.append((old_cap, new_cap))
    return pairs


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
) -> None:
    """Fase 2.5b: Anchor preservation — garantizar GraphIR no vacío.

    SAFE_SKIP nunca puede eliminar TODOS los nodos del grafo.
    Si todas las capabilities resultarían en 0 nodos builder
    (solo KEEP/DELETE, sin CREATE/MODIFY), preservar el anchor.

    Condiciones para preservar:
      1. No hay CREATE/MODIFY → builder produciría 0 nodos
      2. Hay al menos un KEEP (hay intención estructural de preservar)
      3. NO todas son KEEP (si todas son KEEP → noop válido por has_resolved_keep_state)

    Prioridad: layout.page > domain.* > primera capability válida.

    El anchor se marca como MODIFY con SAFE_COMPLETE y params vacíos
    (= preservación estructural, no CREATE forzado).
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

    for rc in resolved:
        if rc.name == "layout.page":
            _preserve_anchor(resolved, rc, warnings)
            return

    for rc in resolved:
        if rc.name.startswith("domain."):
            _preserve_anchor(resolved, rc, warnings)
            return

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

    # Ampliar scope con targets de acciones que no están en capabilities
    # (tanto repo-only como CREATE targets que no existen en repo aún)
    for target in set(action_map) - set(capabilities):
        capabilities.append(target)

    # Extraer layout_hints y replace_pairs de acciones semánticas
    layout_hints: dict[str, dict] = {}
    replace_pairs: list[tuple[str, str]] = []
    if semantic_resolution.actions:
        contract_caps_for_hints = (
            _infer_capabilities_from_contract(contract)
            if semantic_resolution.actions
            else capabilities
        )
        layout_hints = _extract_layout_hints(
            semantic_resolution, contract_caps_for_hints, structural_index,
        )
        replace_pairs = _extract_replace_pairs(
            semantic_resolution, contract_caps_for_hints, structural_index,
        )
        # Añadir nuevas capabilities de replace_pairs al scope
        for _old, new in replace_pairs:
            if new not in capabilities:
                capabilities.append(new)

    # Paso 3: resolver cada capability con lifecycle action (Step B)
    resolved: list[ResolvedCapability] = []

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

        # KEEP / DELETE → no resuelven params (repo tiene la verdad)
        if action in (KEEP, DELETE):
            resolved.append(ResolvedCapability(
                name=cap,
                params={},
                mode=CompletionMode.SAFE_SKIP,
                action=action,
            ))
            if action == DELETE:
                warnings.append(f"{cap}: marked for deletion")
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
        ))

    # Fase 2.5b: Graph viability invariant — anchor preservation
    # Ensure at least one capability produces a builder node
    _ensure_graph_viability(resolved, warnings)

    # Aggregate provenance from all capabilities
    all_provenance: dict[str, str] = {}
    for rc in resolved:
        for k, v in rc.provenance.items():
            if k not in all_provenance:
                all_provenance[k] = v

    replace_pairs_index = {new: old for old, new in replace_pairs}

    return StructuralIR(
        contract_id=contract.contract_id,
        contract_version=contract.version,
        capabilities=tuple(resolved),
        param_provenance=all_provenance,
        confidence=contract_resolution.confidence,
        completion_warnings=tuple(warnings),
        layout_hints=layout_hints,
        replace_pairs=replace_pairs,
        replace_pairs_index=replace_pairs_index,
    )


def graphir_ready_to_intent_plan(ir: StructuralIR) -> IntentPlan:
    """Convert StructuralIR → IntentPlan (execution artifact).

    StructuralIR ya tiene ownership resuelto. Esta conversión existe
    solo como execution artifact para compatibilidad legacy y serialización.
    NO participa en decisiones semánticas.
    """
    contract = get_contract(ir.contract_id, ir.contract_version)
    if contract is None:
        raise ValueError(
            f"Contract not found: "
            f"{ir.contract_id}@{ir.contract_version}"
        )

    intents = []
    for rc in ir.capabilities:
        if rc.mode == CompletionMode.SAFE_SKIP:
            continue
        intents.append(Intent(
            id=make_intent_id(f"completion:{rc.name}", rc.name, "structural"),
            capability=rc.name,
            params=dict(rc.params),
            source="structural_completion",
        ))

    return IntentPlan(
        intents=intents,
        contracts=[contract],
        params={},
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
    """Valida consistencia entre replace_pairs del IR y fileops reales.

    Reglas:
      1. Cada (old, new) en replace_pairs debe tener:
         - Un DELETE fileop para old (o el old debe estar en structural_index)
         - Un CREATE/MODIFY fileop para new
      2. new no debe tener DELETE fileop
      3. old no debe tener CREATE fileop

    Returns lista de warnings (vacía = todo consistente).
    """
    warnings: list[str] = []
    if not structural_ir.replace_pairs:
        return warnings

    ops_by_target: dict[str, list[str]] = {}
    for fop in fileops:
        fop_path = fop.path if hasattr(fop, 'path') else fop.get('path', '')
        fop_action = fop.action if hasattr(fop, 'action') else fop.get('action', '')
        target = _capability_from_path(fop_path)
        if target:
            ops_by_target.setdefault(target, []).append(fop_action)

    for old_cap, new_cap in structural_ir.replace_pairs:
        old_ops = ops_by_target.get(old_cap, [])
        old_exists = structural_index is not None and structural_index.exists(old_cap)
        if "delete" not in old_ops and old_exists:
            warnings.append(
                f"replace_pair ({old_cap}→{new_cap}): "
                f"old '{old_cap}' exists in repo but no DELETE fileop produced"
            )
        new_ops = ops_by_target.get(new_cap, [])
        if "create" not in new_ops and "modify" not in new_ops:
            warnings.append(
                f"replace_pair ({old_cap}→{new_cap}): "
                f"new '{new_cap}' has no CREATE/MODIFY fileop"
            )
        if "delete" in new_ops:
            warnings.append(
                f"replace_pair ({old_cap}→{new_cap}): "
                f"new '{new_cap}' has DELETE fileop — inconsistent"
            )
        if "create" in old_ops:
            warnings.append(
                f"replace_pair ({old_cap}→{new_cap}): "
                f"old '{old_cap}' has CREATE fileop — should be DELETE or no-op"
            )

    return warnings
