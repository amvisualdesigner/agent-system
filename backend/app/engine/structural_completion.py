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


@dataclass(frozen=True)
class ResolvedCapability:
    """Capability con ownership definitivo de params.

    Frozen: immutable después de creación. Ninguna capa posterior puede
    reinterpretar, redistribuir o mutar estos params.
    """
    name: str
    params: dict[str, Any]
    mode: "CompletionMode"
    provenance: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class StructuralIR:
    """Representación estructural final — ownership semántico resuelto.

    Frozen: completamente inmutable. LangGraph-safe. Cacheable.

    capabilities es la única lista — incluye tanto SAFE_COMPLETE como SAFE_SKIP.
    """
    contract_id: str
    contract_version: int
    capabilities: tuple[ResolvedCapability, ...]
    param_provenance: dict[str, str]
    confidence: float
    completion_warnings: tuple[str, ...] = ()


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
) -> StructuralIR:
    """Convierte SemanticResolution + ContractResolution en StructuralIR.

    Args:
        semantic_resolution: Params del lenguaje del usuario.
        contract_resolution: Params del contrato (SkillIR + defaults).
        contract: SkillContract seleccionado.
        frame_dict: Dict del StructuredSemanticFrame (para hint augmentation).

    Returns:
        StructuralIR con capabilities resueltas y ownership definitivo.

    Raises:
        ValueError: si el contrato no es compatible.
    """
    warnings: list[str] = []

    # Paso 1: capabilities base del contrato
    capabilities = _infer_capabilities_from_contract(contract)

    # Paso 2: augmentar con hints del frame
    capabilities = _augment_capabilities(
        capabilities, semantic_resolution, contract_resolution, frame_dict,
    )

    # Paso 3: resolver cada capability
    resolved: list[ResolvedCapability] = []

    for cap in capabilities:
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
            ))
            warnings.append(f"{cap}: skipped")
            continue

        # SAFE_COMPLETE con required faltantes → SAFE_SKIP
        # Structural layer nunca inventa semántica de dominio
        if mode == CompletionMode.SAFE_COMPLETE and missing_required:
            resolved.append(ResolvedCapability(
                name=cap,
                params={},
                mode=CompletionMode.SAFE_SKIP,
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
