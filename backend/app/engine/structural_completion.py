"""StructuralCompletionLayer — SemanticResolution → GraphIRReadyPlan.

Convierte SemanticResolution en un plan completo y ejecutable para GraphIR.
NO decide qué quiere el usuario. NO interpreta intención. NO corrige semántica.
Solo asegura que cada nodo tiene los campos mínimos para no romper GraphIR.

Pipeline:
  SemanticFrame → SemanticResolution → StructuralCompletionLayer → GraphIRPipeline

Responsabilidad única:
  - Valida requisitos mínimos por capability
  - Rellena defaults seguros (deterministas, no semánticos)
  - Expande estructura (no semántica)
  - Asegura GraphIR compatibility
  - Detecta gaps estructurales
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.contracts.semantic_resolution import SemanticResolution
from app.contracts.skill_registry import SkillContract, get_contract
from app.graphir.intent import Intent, IntentPlan, make_intent_id


class StructuralCompletionError(ValueError):
    """Raised when required domain params cannot be auto-completed."""
    pass


class CompletionMode(Enum):
    STRICT_FAIL = "strict_fail"
    SAFE_SKIP = "safe_skip"
    SAFE_COMPLETE = "safe_complete"


@dataclass
class GraphIRReadyPlan:
    contract_id: str
    contract_version: int
    capabilities: list[str]
    params_by_capability: dict[str, dict[str, Any]]
    param_provenance: dict[str, str]
    confidence: float
    completion_warnings: list[str] = field(default_factory=list)


STRUCTURAL_SCHEMA: dict[str, dict[str, Any]] = {
    "presentation.kpi_row": {
        "required": ["metrics"],
        "optional": ["aggregation", "format"],
        "safe_fallback": {"metrics": ["net_revenue"]},
        "mode": CompletionMode.SAFE_COMPLETE,
    },
    "presentation.table": {
        "required": ["columns"],
        "optional": ["table_data", "metrics", "dimensions", "top_k"],
        "safe_fallback": {"columns": ["id"]},
        "mode": CompletionMode.SAFE_COMPLETE,
    },
    "presentation.timeseries": {
        "required": ["metric"],
        "optional": ["time_granularity", "group_by"],
        "safe_fallback": {"metric": "revenue"},
        "mode": CompletionMode.SAFE_COMPLETE,
    },
    "presentation.chart.bar": {
        "required": ["metrics"],
        "optional": ["categories", "top_k"],
        "safe_fallback": {"metrics": ["net_revenue"]},
        "mode": CompletionMode.SAFE_COMPLETE,
    },
    "presentation.metric_card": {
        "required": ["metric"],
        "optional": [],
        "safe_fallback": {"metric": "net_revenue"},
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
    resolution: SemanticResolution,
    frame_dict: dict | None,
) -> list[str]:
    """Soft-add capabilities basadas en el semantic_frame (objetos, no léxico).

    NO usa intents. NO usa keyword decomposition.
    Usa SOLO el frame validado a nivel de objetos.

    Regla:
      - Si frame contiene objeto type="table" y resolution tiene 'columns' o
        'mentioned_metrics' → añadir 'presentation.table' si no está
    """
    if frame_dict is None:
        return base_capabilities

    augmented = set(base_capabilities)
    objects = frame_dict.get("objects", [])

    # Hint: objeto table detectado + señales en resolution
    has_table_object = any(o.get("type") == "table" for o in objects)
    has_table_signal = (
        "columns" in resolution.params
        or "mentioned_metrics" in resolution.params
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


def _resolve_safe_default(capability: str, field: str) -> Any:
    """Resuelve un safe default para un campo requerido faltante.

    SOLO aplica a capabilities presentation.* — domain.* NUNCA usa safe_fallback.
    """
    schema = STRUCTURAL_SCHEMA.get(capability, {})
    fallbacks = schema.get("safe_fallback", {})
    if capability.startswith("domain."):
        raise StructuralCompletionError(
            f"Cannot auto-complete domain field '{field}' for {capability}. "
            "Domain capabilities require explicit params from user or SkillIR."
        )
    if field in fallbacks:
        return fallbacks[field]
    raise StructuralCompletionError(
        f"No safe default available for required field '{field}' in {capability}."
    )


def complete_structure(
    resolution: SemanticResolution,
    contract: SkillContract,
    frame_dict: dict | None = None,
) -> GraphIRReadyPlan:
    """Convierte SemanticResolution en un GraphIRReadyPlan completo.

    Args:
        resolution: SemanticResolution con params del usuario + SkillIR
        contract: SkillContract del contrato seleccionado
        frame_dict: Dict del StructuredSemanticFrame (para hint augmentation)

    Returns:
        GraphIRReadyPlan con params por capability y warnings de completion

    Raises:
        StructuralCompletionError: si domain.* tiene required missing y
            confidence >= 0.6
    """
    warnings: list[str] = []

    # Paso 1: capabilities base del contrato
    capabilities = _infer_capabilities_from_contract(contract)

    # Paso 2: augmentar con hints del frame
    capabilities = _augment_capabilities(capabilities, resolution, frame_dict)

    # Paso 3: completar params por capability
    params_by_capability: dict[str, dict[str, Any]] = {}

    for cap in capabilities:
        schema = STRUCTURAL_SCHEMA.get(cap)
        if schema is None:
            warnings.append(f"{cap}: no structural schema — skipping")
            continue

        mode = _resolve_completion_mode(cap, resolution.confidence)

        if mode == CompletionMode.SAFE_SKIP:
            warnings.append(
                f"{cap}: low confidence ({resolution.confidence:.2f}) — skipped"
            )
            continue

        cap_params: dict[str, Any] = {}

        # Copiar lo que ya existe en resolution.params para esta capability
        for field in schema.get("required", []) + schema.get("optional", []):
            if field in resolution.params:
                cap_params[field] = resolution.params[field]

        # Completar required faltantes
        for field in schema.get("required", []):
            if field not in cap_params:
                if mode == CompletionMode.STRICT_FAIL:
                    raise StructuralCompletionError(
                        f"Missing required field '{field}' for {cap}. "
                        "Cannot auto-complete domain capabilities."
                    )
                try:
                    default = _resolve_safe_default(cap, field)
                    cap_params[field] = default
                    warnings.append(
                        f"{cap}.{field} missing → injected safe default"
                    )
                except StructuralCompletionError:
                    raise

        params_by_capability[cap] = cap_params

    return GraphIRReadyPlan(
        contract_id=resolution.contract_id,
        contract_version=resolution.contract_version,
        capabilities=capabilities,
        params_by_capability=params_by_capability,
        param_provenance=dict(resolution.param_provenance),
        confidence=resolution.confidence,
        completion_warnings=warnings,
    )


def graphir_ready_to_intent_plan(ready: GraphIRReadyPlan) -> IntentPlan:
    """Convert GraphIRReadyPlan → IntentPlan para GraphIRPipeline.

    Cada capability del plan completado se convierte en un Intent
    con sus params por-capability. Los params se aplanan a plan.params
    para compatibilidad con bind_skillir_to_nodes — seguro porque
    dentro de un mismo contrato no hay colisiones de keys entre capabilities.
    """
    contract = get_contract(ready.contract_id, ready.contract_version)
    if contract is None:
        raise ValueError(
            f"Contract not found: "
            f"{ready.contract_id}@{ready.contract_version}"
        )

    intents = [
        Intent(
            id=make_intent_id(f"completion:{cap}", cap, "structural"),
            capability=cap,
            params=dict(ready.params_by_capability.get(cap, {})),
            source="structural_completion",
        )
        for cap in ready.capabilities
    ]

    flat_params: dict[str, Any] = {}
    for cap_params in ready.params_by_capability.values():
        for k, v in cap_params.items():
            flat_params[k] = v

    return IntentPlan(
        intents=intents,
        contracts=[contract],
        params=flat_params,
    )
