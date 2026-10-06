"""Fase 6, Subfase D — Candidate Schema, mapping determinista y matriz 0/1/N.

Cierra la frontera de tipos fijada en el diseño de D:

    SourceSchema.shape      describe la FUENTE   (candidate.schema.shape)
    MappingValueSchema.shape describe el VALOR    (este modulo)
    requirement.expected_shapes  describe el VALOR esperado tras el mapping

`SourceSchema.shape` NUNCA se compara con `expected_shapes`: son etapas
distintas (el objeto que expone campos frente al valor que la prop recibe). La
compatibilidad se evalua solo sobre `MappingValueSchema.shape`.

Ver `tmp/Fase-6-DataBinding-Design-SUBFASE-D-READ-ONLY.md`.

Dos etapas de valor, deliberadamente NO compuestas:

- fisica  (`declared_dataslice`): el valor es ``_pageData.<selector>``; el slice
  ya ES el valor completo, transform "identity", shape "unknown".
- logica  (`existing_registry_binding` / `contract_slot_prop`): el valor es
  ``contract_params[<param>]`` con el transform del registry; el shape es el
  ``type_info`` del registry, que describe la prop POST-transform.

``from_field`` es un nombre de param del contrato, no una ruta dentro del hook
(ver `resolver.py:271`). Por eso `slice.selector="kpiData"` y
`from="metrics"` NO se componen en `kpiData.metrics`.

Si ambas etapas prueban rutas distintas para la misma prop, D no elige: las
emite todas y el recuento fuerza `needs_choice`. Elegir una exigiria decidir
que etapa es autoritativa para el mismo valor, que es una decision
arquitectonica.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.binding.discovery import BindingDiscovery, CandidateSource, rooted_at
from app.binding.requirement import declared_shape, slot_prop_to_param
from app.intent.models import BindingRequirement, BindingProposal, DataMappingEntry, EvidenceItem

STAGE_SLICE = "slice"
STAGE_PARAM = "param"


@dataclass(frozen=True)
class MappingValueSchema:
    """El VALOR que la prop recibe tras el mapping. Unica etapa comparable."""

    shape: str = "unknown"

    @property
    def is_known(self) -> bool:
        return self.shape not in ("", "unknown")


@dataclass(frozen=True)
class CandidateMapping:
    """Un mapping candidato y determinista para una prop. No esta elegido."""

    prop: str
    stage: str
    value_path: str
    transform: str
    value_schema: MappingValueSchema
    evidence: tuple[EvidenceItem, ...] = ()

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (self.prop, self.stage, self.value_path, self.transform)


def _slice_mappings(requirement: BindingRequirement, candidate: CandidateSource, contract=None):
    """Etapa fisica: el slice es el valor completo."""
    component = requirement.target_component
    fields = set(candidate.schema.fields)
    out = []
    for prop, selector in candidate.prop_field_links:
        if prop not in set(requirement.required_props):
            continue
        # El selector es una RUTA dentro del resultado del hook
        # ('chartData.timeseries'), no un campo de primer nivel. Se valida por
        # enraizado, igual que hace discovery; exigir igualdad exacta
        # descartaria en silencio los slices anidados.
        if not rooted_at(selector, tuple(sorted(fields))):
            continue
        shape = "unknown"
        slot_map = slot_prop_to_param(contract, component) if contract is not None else {}
        param = slot_map.get(prop)
        if param and param == selector:
            contract_props = (
                ((getattr(contract, "input_schema", None) or {}) or {}).get("properties") or {}
            )
            shape = declared_shape(contract_props.get(param)) or "unknown"
        out.append(
            CandidateMapping(
                prop=prop,
                stage=STAGE_SLICE,
                value_path=f"_pageData.{selector}",
                transform="identity",
                value_schema=MappingValueSchema(shape),
                evidence=(
                    EvidenceItem(
                        kind="structural",
                        ref=f"candidate:{candidate.source.kind}:{candidate.source.ref}",
                        detail=(
                            f"slice selector {selector} -> {selector}"
                            + (
                                f"; slot param {param} + input_schema confirm {shape}"
                                if param and param == selector and shape != "unknown"
                                else ""
                            )
                        ),
                    ),
                ),
            )
        )
    return out


def _param_mappings(
    requirement: BindingRequirement,
    candidate: CandidateSource,
    v4_bindings: Mapping[str, Mapping[str, object]],
    contract,
):
    """Etapa logica: el valor es contract_params[<param>] con su transform.

    Solo un binding real del registry (`existing_registry_binding`) produce una
    expresion de valor. Un `contract_slot_prop` NO la produce: declara
    prop <- param como enlace, y ese param ya es la ruta del slice cuando el
    selector coincide (caso Fase 1C). Emitir tambien `_pageData.<selector>` y
    `contract_params[<param>]` para la MISMA declaracion crearia una ambiguedad
    artificial entre dos expresiones del mismo dato.
    """
    component = requirement.target_component
    specs = dict(v4_bindings.get(component) or {})

    links: dict[str, set[str]] = {}
    for prop, spec in specs.items():
        from_field = getattr(spec, "from_field", "") or ""
        if from_field:
            links.setdefault(prop, set()).add(from_field)

    out = []
    for prop in sorted(set(requirement.required_props)):
        for param in sorted(links.get(prop, ())):
            spec = specs.get(prop)
            transform = (getattr(spec, "transform", None) or "identity") if spec else "identity"
            shape = declared_shape(getattr(spec, "type_info", None)) if spec else None
            out.append(
                CandidateMapping(
                    prop=prop,
                    stage=STAGE_PARAM,
                    value_path=f"contract_params[{param!r}]",
                    transform=transform,
                    value_schema=MappingValueSchema(shape or "unknown"),
                    evidence=(
                        EvidenceItem(
                            kind="structural",
                            ref=f"registry:components.{component}.props.{prop}",
                            detail=f"from={param} transform={transform}",
                        ),
                    ),
                )
            )
    return out


def build_candidate_mappings(
    requirement: BindingRequirement,
    candidate: CandidateSource,
    *,
    v4_bindings: Mapping[str, Mapping[str, object]] | None = None,
    contract=None,
) -> tuple[CandidateMapping, ...]:
    """Mappings candidatos para un candidato. Ambiguidad se preserva, no se resuelve."""
    mappings = _slice_mappings(requirement, candidate, contract)
    mappings += _param_mappings(requirement, candidate, dict(v4_bindings or {}), contract)
    unique: dict[tuple[str, str, str, str], CandidateMapping] = {}
    for m in mappings:
        unique.setdefault(m.identity, m)
    return tuple(sorted(unique.values(), key=lambda m: m.identity))


def shape_conflicts(
    requirement: BindingRequirement, mappings: tuple[CandidateMapping, ...]
) -> tuple[str, ...]:
    """Props cuya FORMA DEL VALOR probada contradice lo esperado.

    Solo compara `MappingValueSchema.shape` contra `expected_shapes`. Un valor
    `unknown` no contradice: no se puede refutar sin inventar shape.
    """
    conflicts = []
    expected = dict(requirement.expected_shapes)
    for m in mappings:
        want = expected.get(m.prop)
        if want and want != "unknown" and m.value_schema.is_known and m.value_schema.shape != want:
            conflicts.append(m.prop)
    return tuple(sorted(set(conflicts)))


def unconfirmed_shapes(
    requirement: BindingRequirement, mappings: tuple[CandidateMapping, ...]
) -> tuple[str, ...]:
    """Props exigidas cuyo shape del valor no queda confirmado por ningun mapping.

    Bloquea `auto_unique`: la unicidad no se certifica sobre lo no probado.
    """
    by_prop = {m.prop: m for m in mappings}
    out = []
    for prop in requirement.required_props:
        m = by_prop.get(prop)
        if m is None or not m.value_schema.is_known:
            out.append(prop)
    return tuple(sorted(set(out)))


def derive_binding_proposal(
    discovery: BindingDiscovery,
    *,
    v4_bindings: Mapping[str, Mapping[str, object]] | None = None,
    contract=None,
) -> BindingProposal:
    """Matriz 0/1/N. Estructural, sin scoring, sin elegir entre candidatos."""
    requirement = discovery.requirement
    entries: list[DataMappingEntry] = []
    conflicts: list[str] = []
    unconfirmed: list[str] = []
    ambiguous_props: set[str] = set()

    per_candidate = []
    for candidate in discovery.candidates:
        mappings = build_candidate_mappings(
            requirement, candidate, v4_bindings=v4_bindings, contract=contract
        )
        per_candidate.append((candidate, mappings))

    # Ambiguedad de etapa: >1 mapping distinto para la misma prop.
    for _candidate, mappings in per_candidate:
        counts: dict[str, set] = {}
        for m in mappings:
            counts.setdefault(m.prop, set()).add((m.stage, m.value_path, m.transform))
        ambiguous_props |= {p for p, c in counts.items() if len(c) > 1}

    # Entradas/material solo cuando hay EXACTAMENTE un candidato y ningun
    # mapping ambiguo; en 0/N el mapping sigue sin elegir.
    chosen = per_candidate[0] if len(per_candidate) == 1 and not ambiguous_props else None
    if chosen is not None:
        candidate, mappings = chosen
        conflicts = list(shape_conflicts(requirement, mappings))
        unconfirmed = list(unconfirmed_shapes(requirement, mappings))
        for m in mappings:
            entries.append(
                DataMappingEntry(
                    prop=m.prop,
                    from_field=m.value_path,
                    transform=m.transform,
                )
            )

    if not per_candidate:
        status = "unresolved"
    elif len(per_candidate) > 1 or ambiguous_props or conflicts:
        status = "needs_choice" if (len(per_candidate) > 1 or ambiguous_props) else "unresolved"
    elif unconfirmed or requirement.incompatibilities:
        status = "unresolved"
    else:
        status = "auto_unique"

    incompatibilities = tuple(
        sorted(
            set(requirement.incompatibilities)
            | {f"shape_conflict:{requirement.target_component}.{p}" for p in conflicts}
            | {f"shape_unconfirmed:{requirement.target_component}.{p}" for p in unconfirmed}
        )
    )

    return BindingProposal(
        status=status,
        target_component=requirement.target_component,
        source=chosen[0].source if chosen is not None else None,
        schema=chosen[0].schema if chosen is not None else None,
        mapping=tuple(entries),
        evidence=tuple(
            e for _c, mappings in per_candidate for m in mappings for e in m.evidence
        ),
        # N candidatos y la ambiguedad viven en provenance: BindingProposal
        # tiene source/schema singulares a proposito (una propuesta materializa
        # UN candidato), y el recuento no es un mapping nuevo.
        provenance={
            "candidate_count": len(per_candidate),
            "candidates": [
                {
                    "kind": c.source.kind,
                    "ref": c.source.ref,
                    "selector": c.source.selector,
                    "schema": c.schema.to_dict(),
                    # Mappings deterministas de ESTA candidata: la seleccion
                    # humana de `needs_choice` se valida contra esta lista sin
                    # redescubrir. No son una eleccion: `mapping` (la entrada
                    # materializada) sigue vacio hasta que haya 1 sola
                    # candidata.
                    "mappings": [
                        {
                            "prop": m.prop,
                            "stage": m.stage,
                            "value_path": m.value_path,
                            "transform": m.transform,
                            "shape": m.value_schema.shape,
                        }
                        for m in mappings
                    ],
                }
                for c, mappings in per_candidate
            ],
            "ambiguous_props": sorted(ambiguous_props),
            "incompatibilities": list(incompatibilities),
            "shape_conflicts": list(conflicts),
            "shape_unconfirmed": list(unconfirmed),
        },
    )