"""StructuralCompletionLayer — SemanticResolution → StructuralIR.

Convierte SemanticResolution en StructuralIR: la representación estructural
final donde cada capability tiene ownership definitivo de sus params.
NO decide qué quiere el usuario. NO interpreta intención. NO corrige semántica.
Solo asegura que cada nodo tiene los campos mínimos para no romper GraphIR.

Pipeline:
  SemanticFrame → SemanticResolution → StructuralCompletionLayer → GraphIRPipeline

StructuralIR es la ÚLTIMA representación donde existe ownership semántico real.
A partir de aquí: UI IR solo construye, no decide.

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


class CompletionMode(Enum):
    STRICT_FAIL = "strict_fail"
    SAFE_SKIP = "safe_skip"
    SAFE_COMPLETE = "safe_complete"


@dataclass
class ResolvedCapability:
    """Capability con ownership definitivo de params después de Structural IR.

    Esta es la unidad base de StructuralIR. Cada ResolvedCapability tiene
    ownership exclusivo de sus params — ninguna capa posterior debe
    reinterpretarlos o redistribuirlos.
    """
    name: str
    params: dict[str, Any]
    mode: "CompletionMode"
    provenance: dict[str, str] = field(default_factory=dict)


@dataclass
class StructuralIR:
    """Representación estructural final — ownership semántico resuelto.

    Única fuente de verdad para qué capabilities existen, qué params tiene
    cada una, y en qué modo de completitud se encuentran.

    NO tiene dicts paralelos. NO tiene flattening. NO permite reinterpretación.
    capabilities es la única lista — incluye tanto SAFE_COMPLETE como SAFE_SKIP.
    """
    contract_id: str
    contract_version: int
    capabilities: list[ResolvedCapability]
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
        raise ValueError(
            f"Cannot auto-complete domain field '{field}' for {capability}. "
            "Domain capabilities require explicit params from user."
        )
    if field in fallbacks:
        return fallbacks[field]
    raise ValueError(
        f"No safe default available for required field '{field}' in {capability}."
    )


def complete_structure(
    resolution: SemanticResolution,
    contract: SkillContract,
    frame_dict: dict | None = None,
) -> StructuralIR:
    """Convierte SemanticResolution en StructuralIR.

    Args:
        resolution: SemanticResolution con params del usuario + SkillIR
        contract: SkillContract del contrato seleccionado
        frame_dict: Dict del StructuredSemanticFrame (para hint augmentation)

    Returns:
        StructuralIR con capabilities resueltas y ownership definitivo
    """
    warnings: list[str] = []

    # Paso 1: capabilities base del contrato
    capabilities = _infer_capabilities_from_contract(contract)

    # Paso 2: augmentar con hints del frame
    capabilities = _augment_capabilities(capabilities, resolution, frame_dict)

    # Paso 3: resolver cada capability
    resolved: list[ResolvedCapability] = []

    for cap in capabilities:
        schema = STRUCTURAL_SCHEMA.get(cap)
        if schema is None:
            warnings.append(f"{cap}: no structural schema — skipping")
            continue

        # Build initial params from resolution
        cap_params: dict[str, Any] = {}
        for field in schema.get("required", []) + schema.get("optional", []):
            if field in resolution.params:
                cap_params[field] = resolution.params[field]

        missing_required = [r for r in schema.get("required", []) if r not in cap_params]

        # Runtime mode override: domain.* with missing required → SAFE_SKIP
        mode = _resolve_completion_mode(cap, resolution.confidence)
        if mode == CompletionMode.STRICT_FAIL and missing_required:
            mode = CompletionMode.SAFE_SKIP

        if mode == CompletionMode.SAFE_SKIP:
            resolved.append(ResolvedCapability(
                name=cap,
                params={},
                mode=mode,
            ))
            warnings.append(f"{cap}: skipped")
            continue

        # Completar required faltantes
        for field in schema.get("required", []):
            if field not in cap_params:
                try:
                    default = _resolve_safe_default(cap, field)
                    cap_params[field] = default
                    warnings.append(
                        f"{cap}.{field} missing → injected safe default"
                    )
                except ValueError:
                    raise

        resolved.append(ResolvedCapability(
            name=cap,
            params=cap_params,
            mode=mode,
            provenance=dict(resolution.param_provenance),
        ))

    return StructuralIR(
        contract_id=resolution.contract_id,
        contract_version=resolution.contract_version,
        capabilities=resolved,
        param_provenance=dict(resolution.param_provenance),
        confidence=resolution.confidence,
        completion_warnings=warnings,
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
