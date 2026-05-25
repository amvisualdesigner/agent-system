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

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.contracts.semantic_resolution import SemanticResolution
from app.contracts.contract_resolution import ContractResolution
from app.contracts.skill_registry import SkillContract, get_contract
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
    repo_state: set[str] | None = None,
) -> list[str]:
    """Validate StructuralIR.operations against spec invariants.

    Rules (non-negotiable):
      1. All actions must be in {CREATE, MODIFY, DELETE, KEEP}
      2. No duplicate targets
      3. If repo_state provided:
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

        # Rule 3: repo_state consistency
        if repo_state is not None:
            exists = target in repo_state
            if action == CREATE and exists:
                warnings.append(
                    f"CREATE '{target}' but already exists in repo"
                )
            elif action in (MODIFY, DELETE, KEEP) and not exists:
                warnings.append(
                    f"{action} '{target}' but not found in repo_state"
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
    """
    contract_id: str
    contract_version: int
    capabilities: tuple[ResolvedCapability, ...]
    param_provenance: dict[str, str]
    confidence: float
    completion_warnings: tuple[str, ...] = ()

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
    "override", "overwrite", "replace", "use instead",
})
_VERBS_DELETE = frozenset({"remove", "delete", "destroy"})
_VERBS_CREATE = frozenset({
    "add", "create", "show", "build", "generate",
    "compose", "design", "include", "insert",
})


def validate_contract_repo_consistency(
    contract_caps: list[str],
    repo_state: set[str],
    contract_id: str = "",
) -> list[str]:
    """Validación bidireccional contrato ↔ repositorio.

    Rules:
      1. Every capability in contract must have a node in repo_state
      2. Every node in repo_state must map to a contract capability

    Returns list of warnings (vacía si todo está consistente).
    No bloquea — solo advierte.
    """
    warnings: list[str] = []

    if not repo_state:
        return warnings

    contract_set = set(contract_caps)
    # Rule 1: contract → repo
    for cap in contract_set:
        if cap not in repo_state:
            warnings.append(
                f"[contract-repo] {contract_id}: capability '{cap}' "
                f"declared in contract but not found in repo"
            )

    # Rule 2: repo → contract
    for cap in repo_state:
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
    repo_state: set[str] | None = None,
) -> dict[str, str]:
    """Step A: Match action objects to capability names.

    El universo de targets posibles es repo_state ∪ contract_caps.
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
    if repo_state:
        all_targets |= repo_state
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
    repo_state: set[str] | None,
) -> str:
    """Step B: Determinar lifecycle action para una capability.

    Regla determinista:
      - existe en repo + verb delete → DELETE
      - existe en repo + verb modify → MODIFY
      - existe en repo + sin verb  → KEEP
      - no existe + verb create    → CREATE
      - no existe + sin verb       → CREATE (contract default)
    """
    exists = repo_state is not None and capability in repo_state

    if action_verb:
        vl = action_verb.lower()
        if vl in _VERBS_DELETE:
            return DELETE if exists else KEEP
        if vl in _VERBS_MODIFY:
            return MODIFY if exists else CREATE
        if vl in _VERBS_CREATE:
            return CREATE
        if exists:
            return MODIFY  # unrecognized verb on existing → modify

    if exists:
        return KEEP
    return CREATE


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


def complete_structure(
    semantic_resolution: SemanticResolution,
    contract_resolution: ContractResolution,
    contract: SkillContract,
    frame_dict: dict | None = None,
    repo_state: set[str] | None = None,
) -> StructuralIR:
    """Convierte SemanticResolution + ContractResolution en StructuralIR.

    Args:
        semantic_resolution: Params del lenguaje del usuario + actions.
        contract_resolution: Params del contrato (SkillIR + defaults).
        contract: SkillContract seleccionado.
        frame_dict: Dict del StructuredSemanticFrame (para hint augmentation).
        repo_state: Conjunto de capability names que ya existen en el repo.
                    Si es None, se asume repositorio vacío (todo CREATE).

    Returns:
        StructuralIR con capabilities resueltas y ownership definitivo.

    Raises:
        ValueError: si el contrato no es compatible.
    """
    warnings: list[str] = []

    # Paso 0: validación consistencia contrato ↔ repositorio
    if repo_state is not None:
        contract_caps = _infer_capabilities_from_contract(contract)
        warnings.extend(validate_contract_repo_consistency(
            contract_caps, repo_state, contract.contract_id,
        ))

    # Paso 1: scope bootstrap según modo
    # Operational mode (con acciones): actions + repo definen scope
    # Declarative mode (sin acciones): contrato define scope
    if semantic_resolution.actions:
        capabilities = []
    else:
        capabilities = _infer_capabilities_from_contract(contract)
        capabilities = _augment_capabilities(
            capabilities, semantic_resolution, contract_resolution, frame_dict,
        )

    # Paso 2b: Step A — match actions from semantic layer to capabilities
    # El universo de matching SIEMPRE es repo ∪ contract
    # (necesitamos contract_caps para resolver nombres de capability aunque
    # en operational mode el scope de iteración sea distinto)
    contract_caps_for_matching = (
        _infer_capabilities_from_contract(contract)
        if semantic_resolution.actions
        else capabilities
    )
    action_map = _match_actions_to_capabilities(
        semantic_resolution.actions, contract_caps_for_matching, contract,
        repo_state=repo_state,
    )

    # Ampliar scope con targets de acciones que no están en capabilities
    # (tanto repo-only como CREATE targets que no existen en repo aún)
    for target in set(action_map) - set(capabilities):
        capabilities.append(target)

    # Paso 3: resolver cada capability con lifecycle action (Step B)
    resolved: list[ResolvedCapability] = []

    for cap in capabilities:
        action_verb = action_map.get(cap)
        action = _resolve_action(cap, action_verb, repo_state)

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
