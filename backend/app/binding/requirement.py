"""BindingRequirement — construcción determinista del requisito del consumidor.

Fase 6 · Subfase B.

Este módulo responde UNA pregunta: **¿qué necesita este componente antes
de elegirle una fuente de datos?** No descubre fuentes, no lee schemas de
candidatos y no construye mappings. El mapping es resultado de la
evaluación posterior (Subfase C), nunca un input circular.

Evidencia reutilizada (sin structures duplicadas):
  - `contract.capability_param_map` / `ast_template.slots[].props`
    → qué props del consumidor se alimentan de qué contract param.
  - `app.signature.extractor.extract_signatures`
    → props reales del componente (`prop_names`, `required_props`,
      `optional_props`, bloque `props`).
  - `app.signature.prop_mapper.load_v4_bindings` → registry v4
    (`from`, `transform`, `arity`, `shape`, `type_info`, `required`).

Reglas duras:

1. `components.X.props = {}` en data_access.json significa AUSENCIA DE
   DECLARACIONES DE BINDING v4, nunca "el componente no tiene props".
   Para `FilterPanel` la evidencia de props viene del slot del contrato.

2. `required_props` se toma de las fuentes que declaran conexión a datos
   (slot del contrato, registry `required: true`). La firma NO es fuente
   de required_props: no distingue props de config de props de datos, y
   usarla fabricaría requisitos (p.ej. `Timeseries.title`). La firma se
   usa como VALIDADOR: si declara no aceptar una prop que el slot exige,
   la evidencia es incompatible y se registra.

3. `expected_shapes` solo se emite si TODAS las declaraciones disponibles
   para esa prop coinciden. No hay prioridad entre fuentes: si el registry
   (tipo de la prop post-transform) y el `input_schema` del contrato (tipo
   del param) discrepan, son etapas distintas y se registra la
   incompatibilidad en vez de elegir una. No se inventa ningún shape.

4. Sin evidencia suficiente → no se fabrica requisito.
"""

from __future__ import annotations

import logging
import re

from app.intent.models import BindingRequirement

logger = logging.getLogger(__name__)


# ── Signature prop types ───────────────────────────────────────────
# Reutiliza el patrón del extractor para no duplicar el parsing de
# interfaces TSX que ya existe y está validado por tests.
try:  # pragma: no cover - import guard
    from app.signature.extractor import _PROP_DETAIL_PATTERN
except Exception:  # pragma: no cover
    _PROP_DETAIL_PATTERN = re.compile(
        r"^\s+(\w+)(\??)\s*:\s*(.+?);?\s*$", re.MULTILINE
    )


_PRIMITIVES = ("string", "number", "boolean", "object", "unknown")


def _shape_from_declared_type(declared) -> str | None:
    """Normaliza una declaración de tipo (dict JSON-schema o string TS).

    Dict  → {type: array, items: {type: string}}  == "array<string>"
    Str   → "string[]" / "Array<string>" / "string" == "array<string>" / "string"
    Union → ["string", "number"] == "number|string" (orden estable)
    """
    if isinstance(declared, dict):
        base = declared.get("type")
        if isinstance(base, list):
            parts = sorted(str(b) for b in base)
            return "|".join(parts) if parts else None
        if base == "array":
            items = declared.get("items")
            inner = items.get("type") if isinstance(items, dict) else items
            if isinstance(inner, str) and inner:
                return f"array<{inner}>"
            return "array"
        if isinstance(base, str):
            return base
        return None

    if isinstance(declared, str):
        text = declared.strip().rstrip(";").strip()
        if not text:
            return None
        if text.endswith("[]"):
            inner = text[:-2].strip()
            return f"array<{inner}>" if inner else "array"
        match = re.match(r"^Array\s*<\s*(.+?)\s*>$", text)
        if match:
            return f"array<{match.group(1)}>"
        match = re.match(r"^(\w[\w<>\[\]\s]*?)\s*\|\s*(\w[\w<>\[\]\s]*)$", text)
        if match:
            return "|".join(sorted([match.group(1).strip(), match.group(2).strip()]))
        if text in _PRIMITIVES:
            return text
        return text

    return None


def _signature_prop_types(signature: dict | None) -> dict[str, str]:
    """Extrae {prop: tipo declarado} del bloque `props` de una firma real."""
    if not signature:
        return {}
    block = signature.get("props") or ""
    if not isinstance(block, str) or not block.strip():
        return {}
    out: dict[str, str] = {}
    for match in _PROP_DETAIL_PATTERN.finditer(block):
        name, _optional, declared = match.group(1), match.group(2), match.group(3)
        shape = _shape_from_declared_type(declared)
        if shape:
            out.setdefault(name, shape)
    return out


def _slot_prop_to_param(contract, target_component: str) -> dict[str, str]:
    """{prop del consumidor: contract_param} desde evidencia del contrato.

    Reutiliza `capability_param_map` (precompilado en `SkillContract`), que
    es exactamente el resultado de compilar `ast_template.slots[].props`.
    Si la capability no es resoluble, escanea los slots por `type`. Ambas
    rutas son la MISMA evidencia, no una prioridad.
    """
    if contract is None:
        return {}
    capabilities = (contract.ast_template or {}).get("capabilities") or {}
    capability = capabilities.get(target_component)
    if capability:
        compiled = (contract.capability_param_map or {}).get(capability)
        if compiled:
            return dict(compiled)
    for slot in (contract.ast_template or {}).get("slots") or []:
        if slot.get("type") == target_component:
            return dict(slot.get("props") or {})
    return {}


def build_binding_requirement(
    target_component: str,
    contract=None,
    signature: dict | None = None,
    v4_bindings: dict | None = None,
) -> BindingRequirement:
    """Construye el requisito determinista de un componente consumidor.

    Args:
        target_component: nombre del tipo de componente (p.ej. "FilterPanel").
        contract: SkillContract del repo (opcional).
        signature: firma real del componente desde `extract_signatures`
                   (opcional; si se omite, no se valida contra el .tsx).
        v4_bindings: resultado de `load_v4_bindings()` (opcional).

    Returns:
        BindingRequirement con required_props, expected_shapes y las
        incompatibilidades detectadas. Nunca lanza por evidencia pobre:
        la evidencia insuficiente se omite y se registra.
    """
    incompatibilities: list[str] = []

    prop_to_param = _slot_prop_to_param(contract, target_component)
    registry_props = (v4_bindings or {}).get(target_component) or {}

    # ── Candidatas: solo fuentes que declaran conexión a datos ──
    candidates: list[str] = []
    for prop in prop_to_param:
        if prop not in candidates:
            candidates.append(prop)
    for prop, binding in registry_props.items():
        if getattr(binding, "required", False) and prop not in candidates:
            candidates.append(prop)

    # ── Validador: la firma real debe aceptar la prop exigida ──
    signature_props = set((signature or {}).get("prop_names") or [])
    signature_types = _signature_prop_types(signature)

    accepted: list[str] = []
    for prop in candidates:
        if signature_props and prop not in signature_props:
            incompatibilities.append(
                f"slot_or_registry_requires_prop_absent_from_signature:"
                f"{target_component}.{prop}"
            )
            continue
        accepted.append(prop)

    # ── Shapes: solo si todas las declaraciones coinciden ──
    shapes: list[tuple[str, str]] = []
    contract_props = ((contract.input_schema if contract else {}) or {}).get("properties") or {}

    for prop in accepted:
        declarations: list[str] = []

        binding = registry_props.get(prop)
        if binding is not None:
            declared = _shape_from_declared_type(getattr(binding, "type_info", None))
            if declared:
                declarations.append(declared)
            declared = _shape_from_declared_type(getattr(binding, "shape", None))
            if declared:
                declarations.append(declared)

        if prop in signature_types:
            declarations.append(signature_types[prop])

        param = prop_to_param.get(prop)
        if param and param in contract_props:
            declared = _shape_from_declared_type(contract_props[param])
            if declared:
                declarations.append(declared)

        if not declarations:
            continue

        distinct = sorted(set(declarations))
        if len(distinct) > 1:
            incompatibilities.append(
                f"conflicting_shape_declarations:{target_component}.{prop}="
                + "|".join(distinct)
            )
            continue

        shapes.append((prop, distinct[0]))

    return BindingRequirement(
        target_component=target_component,
        required_props=tuple(accepted),
        expected_shapes=tuple(shapes),
        incompatibilities=tuple(incompatibilities),
    )