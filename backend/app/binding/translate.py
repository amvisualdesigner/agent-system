"""Fase 6, Subfase E — DataBinding confirmado como autoridad semantica.

Frontera explícita, exigida por el usuario:

    Confirmed DataBinding  = AUTORIDAD SEMANTICA  (lo congeló la confirmacion)
    ResolvedBindings       = REPRESENTACION FISICA para el renderer

El renderer traduce el binding confirmado a su representacion existente. NO lo
reinterpreta, NO lo sustituye por el binding del registry, y NO elige entre
expresiones por conveniencia del renderer.

Traduccion fisica determinista (NO una eleccion):

    from_field == "_pageData.<selector>"  ->  JSVariable("_pageData.<selector>")

Verificado contra el resolutor existente sobre el registry real: para
FilterPanel, `resolver.resolve({})` ya emite exactamente
`JSVariable(name='_pageData.filters')` con provenance `slice:filters`. El
binding congelado dice `_pageData.filters`. Es la MISMA expresion: la
traduccion es identidad, no una eleccion entre etapas.

Si `from_field` tiene otra forma, la traduccion NO intenta reconstruirla ni
adivinar: se levanta `UntranslatableBinding`. Ese caso exigiria decidir que
etapa es la autoridad fisica, que es una decision arquitectonica.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.binding.models import BindingProvenance, ResolvedBindings
from app.graphir.backends.react_backend import JSVariable
from app.intent.models import DataBinding, DataMappingEntry, IntentAction

PAGE_DATA_PREFIX = "_pageData."


class UntranslatableBinding(ValueError):
    """El binding congelado no tiene traduccion fisica determinista.

    Se levanta en vez de reinterpretar. Reinterpretar seria sustituir la
    autoridad semantica por conveniencia del renderer.
    """


@dataclass(frozen=True)
class PhysicalBinding:
    """Representacion fisica de UNA entrada de mapping confirmada."""

    component: str
    prop: str
    value: JSVariable
    transform: str
    provenance: str


def _translate_entry(component: str, entry: DataMappingEntry) -> tuple[str, PhysicalBinding]:
    if not entry.from_field.startswith(PAGE_DATA_PREFIX):
        raise UntranslatableBinding(
            f"{component}.{entry.prop}: from_field {entry.from_field!r} is not a "
            f"{PAGE_DATA_PREFIX}* page-data path. Translating it would require "
            "deciding which value stage is physically authoritative, which is an "
            "architectural decision, not a translation."
        )
    if entry.transform not in (None, "identity"):
        raise UntranslatableBinding(
            f"{component}.{entry.prop}: transform {entry.transform!r} is not a "
            "no-op. Re-applying it would reinterpret the frozen binding."
        )
    physical = PhysicalBinding(
        component=component,
        prop=entry.prop,
        value=JSVariable(entry.from_field),
        transform="identity",
        provenance=str(
            BindingProvenance(
                source="confirmed_binding",
                selector=entry.from_field[len(PAGE_DATA_PREFIX):],
                contract_params=[],
            )
        ),
    )
    return entry.prop, physical


def translate_confirmed_binding(binding: DataBinding, component: str) -> dict[str, PhysicalBinding]:
    """Traduce un DataBinding confirmado a su representacion fisica.

    Determinista y total para la forma que el resolver ya emite. No consulta el
    registry, no conoce transforms, no reinterpreta nada.
    """
    out: dict[str, PhysicalBinding] = {}
    for entry in binding.mapping:
        prop, physical = _translate_entry(component, entry)
        if prop in out and out[prop] != physical:
            raise UntranslatableBinding(
                f"{component}.{prop}: conflicting frozen entries for one prop"
            )
        out[prop] = physical
    return out


def confirmed_bindings_from_actions(actions: list[IntentAction]) -> dict[str, DataBinding]:
    """Componente -> binding confirmado. Acciones sin binding se omiten."""
    out: dict[str, DataBinding] = {}
    for action in actions:
        if action.binding is None:
            continue
        component = action.target_capability or action.verb or ""
        if not component:
            continue
        out[component] = action.binding
    return out


def apply_confirmed_bindings(
    resolved: ResolvedBindings, confirmed: dict[str, DataBinding]
) -> ResolvedBindings:
    """Overlay del binding confirmado sobre la representacion fisica existente.

    El binding confirmado SOBRESCRIBE la prop porque es la autoridad semantica.
    No se recalcula nada, no se consulta el registry para "corregirlo", y las
    props no confirmadas quedan intactas: `needs_choice` y `unresolved` nunca
    producen overlay, asi que el renderer sigue viendo su resolucion actual.
    """
    if not confirmed:
        return resolved

    component_props = {c: dict(p) for c, p in resolved.component_props.items()}
    provenance = {c: dict(p) for c, p in resolved.provenance.items()}

    for component, binding in sorted(confirmed.items()):
        physical = translate_confirmed_binding(binding, component)
        if component not in component_props:
            component_props[component] = {}
        if component not in provenance:
            provenance[component] = {}
        for prop, pb in sorted(physical.items()):
            component_props[component][prop] = pb.value
            provenance[component][prop] = pb.provenance

    return ResolvedBindings(
        component_props=component_props,
        provenance=provenance,
        consumed_params=set(resolved.consumed_params),
        unconsumed_params=set(resolved.unconsumed_params),
        page_data_source=resolved.page_data_source,
        imports=list(resolved.imports),
    )