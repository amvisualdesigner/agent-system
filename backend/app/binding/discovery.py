"""Deterministic candidate discovery for Data Binding (Fase 6, Subfase C).

Answers exactly one question: *which data sources are demonstrably connected to
this target component?* Nothing more.

Deliberately NOT in this module (belongs to later subphases):

- No mapping selection: candidates carry declarative prop/field *links*, never a
  chosen ``DataMappingEntry``.
- No ``DataBinding``, no ``BindingProposal.status``: 0/1/N resolution needs
  mapping determinism (Subfase D input) and schema compatibility.
- No scoring, no ranking, no priority between relations. When two relations
  cannot be *proven* equal they stay two candidates and a human decides.

Five closed relations, and what each can actually instantiate:

===========================  ==========================  ==================
relation                     source identity            schema evidence
===========================  ==========================  ==================
declared_dataslice           slice (selector)           slice selectors
existing_registry_binding    none (param only)          none
existing_import             symbol                     none (no fields)
existing_call                symbol (with the import)   destructured fields
contract_slot_prop           none (param only)          none
===========================  ==========================  ==================

A candidate needs *both* a source identity and schema evidence, because the
design admits a candidate only when a relation exists, schema/source evidence
exists, and it satisfies the ``BindingRequirement``. So ``existing_registry_binding``
and ``contract_slot_prop`` cannot invent a source: they are recorded as
supporting evidence on candidates instead. That is a type constraint on
``DataSourceRef``, not a preference between relations.

Cross-relation identity is *proven*, never assumed. A slice and a hook call
describe the same physical source only when every slice selector is rooted at a
field the call actually destructures (selectors are paths into the hook result).
Unprovable slice/hook pairs stay separate candidates, which can only make the
result *more* conservative (a human is asked), never wrong.

All functions are pure: file contents are passed in already read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Mapping, Sequence

from app.binding.requirement import slot_prop_to_param
from app.intent.models import DataSchemaRef, DataSourceRef, EvidenceItem, BindingRequirement

CLOSED_RELATIONS = (
    "declared_dataslice",
    "existing_registry_binding",
    "existing_import",
    "existing_call",
    "contract_slot_prop",
)

# import { a, b as c } from 'mod'   /   import a from 'mod'
_IMPORT_NAMED = re.compile(r"import\s*\{([^}]*)\}\s*from\s*['\"]([^'\"]+)['\"]")
_IMPORT_DEFAULT = re.compile(r"import\s+(\w+)\s+from\s*['\"]([^'\"]+)['\"]")
# const { a, b: c, ...rest } = call(...)
_CALL_DESTRUCTURE = re.compile(r"\{([^{}]*)\}\s*=\s*(\w+)\s*\(")
# import type { X } ... -> keep types out of runtime candidates
_TYPE_ONLY = re.compile(r"^type\s+")


def _safe(fn, fallback):
    """Run an evidence accessor; malformed input is omitted, never raised."""
    try:
        return fn()
    except Exception:
        return fallback


def _ident(raw: str) -> str:
    """Local binding name: 'b as c' -> 'c'; ':' destructuring -> 'c'."""
    name = raw.split(" as ", 1)[-1]
    name = name.split(":", 1)[-1]
    name = name.split("=", 1)[0]
    return name.strip().strip(".")


def scan_imports(content: str) -> tuple[tuple[str, str], ...]:
    """Runtime symbols imported: ((local_name, module_path), ...). Sorted, deduped."""
    found: dict[str, str] = {}
    for block, module in _IMPORT_NAMED.findall(content):
        for raw in block.split(","):
            if not raw.strip() or _TYPE_ONLY.match(raw.strip()):
                continue
            name = _ident(raw)
            if name:
                found.setdefault(name, module)
    for name, module in _IMPORT_DEFAULT.findall(content):
        found.setdefault(name, module)
    return tuple(sorted(found.items()))


def scan_calls(content: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Destructured calls: ((callee, fields...), ...). Sorted, deduped."""
    found: dict[str, set[str]] = {}
    for block, callee in _CALL_DESTRUCTURE.findall(content):
        fields = {f for f in (_ident(r) for r in block.split(",")) if f}
        if fields:
            found.setdefault(callee, set()).update(fields)
    return tuple(sorted((c, tuple(sorted(f))) for c, f in found.items()))


def rooted_at(selector: str, fields: Sequence[str]) -> bool:
    """True when selector is a path into the hook result proven by `fields`.

    'filters' and 'chartData.timeseries' are both rooted at a destructured name.
    A selector naming a field the call never destructures is NOT proven.
    """
    root = selector.split(".", 1)[0]
    return bool(root) and root in fields


@dataclass(frozen=True)
class CandidateSource:
    """A demonstrably connected source. NOT a selected binding."""

    source: DataSourceRef
    schema: DataSchemaRef
    relations: tuple[str, ...]
    prop_field_links: tuple[tuple[str, str], ...]
    evidence: tuple[EvidenceItem, ...]


@dataclass(frozen=True)
class BindingDiscovery:
    """Discovery outcome for one target component.

    ``candidates`` is a set, never a ranking: discovery reports what exists.
    """

    target_component: str
    requirement: BindingRequirement
    candidates: tuple[CandidateSource, ...]
    requirement_incompatibilities: tuple[str, ...]
    excluded_candidates: tuple[CandidateSource, ...] = ()

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)


def _hook_candidates(
    target_file_contents: Mapping[str, str],
) -> dict[str, tuple[tuple[str, ...], tuple[EvidenceItem, ...]]]:
    """Symbol -> (proven fields, evidence) from import+call in the target files."""
    imports: dict[str, list[tuple[str, str]]] = {}
    calls: dict[str, set[str]] = {}
    for path, content in sorted(target_file_contents.items()):
        for name, module in scan_imports(content):
            imports.setdefault(name, []).append((path, module))
        for callee, fields in scan_calls(content):
            calls.setdefault(callee, set()).update(fields)

    out: dict[str, tuple[tuple[str, ...], tuple[EvidenceItem, ...]]] = {}
    for symbol in sorted(calls):
        if symbol not in imports:
            continue  # a call to something never imported proves no source link
        prov = imports[symbol]
        fields = tuple(sorted(calls[symbol]))
        evidence = tuple(
            [
                EvidenceItem(
                    kind="structural",
                    ref=f"file:{path}",
                    detail=f"imports {symbol}" + (f" from {module}" if module else ""),
                )
                for path, module in prov
            ]
            + [
                EvidenceItem(
                    kind="structural",
                    ref=f"file:{path}",
                    detail=f"call {symbol}() destructures {', '.join(fields)}",
                )
                for path in sorted(target_file_contents)
                if any(
                    callee == symbol for callee, _ in scan_calls(target_file_contents[path])
                )
            ]
        )
        out[symbol] = (fields, evidence)
    return out


def _merge_into_hook(
    slice_links: tuple[tuple[str, str], ...],
    fields: tuple[str, ...],
) -> bool:
    """True when every slice selector is proven rooted at the hook's fields."""
    return bool(slice_links) and all(
        rooted_at(selector, fields) for _, selector in slice_links
    )


def discover_binding_candidates(
    requirement: BindingRequirement,
    *,
    contract: object | None = None,
    v4_bindings: Mapping[str, Mapping[str, object]] | None = None,
    page_data_source: object | None = None,
    target_file_contents: Mapping[str, str] | None = None,
) -> BindingDiscovery:
    """Enumerate candidate sources for ``requirement`` via the five closed relations.

    Pure. Never raises on malformed evidence; anything unproven is omitted.
    """
    component = requirement.target_component
    files = _safe(lambda: dict(target_file_contents or {}), {})
    v4 = _safe(lambda: dict(v4_bindings or {}), {})

    # ── Relation: declared_dataslice (registry composition) ────────────────
    slice_links: list[tuple[str, str]] = []  # (target_prop, selector)
    for sl in _safe(lambda: list(getattr(page_data_source, "slices", ()) or ()), []):
        if _safe(lambda: getattr(sl, "component", ""), "") != component:
            continue
        target_prop = _safe(lambda: getattr(sl, "target_prop", "") or "", "")
        selector = _safe(lambda: getattr(sl, "selector", "") or "", "")
        if target_prop and selector:
            slice_links.append((target_prop, selector))
    slice_links.sort()

    # ── Relations: existing_registry_binding / contract_slot_prop (links) ───
    # These carry prop->field/param links but no source identity: evidence only.
    registry_links: list[tuple[str, str]] = []
    for prop, spec in sorted(_safe(lambda: dict(v4.get(component) or {}), {}).items()):
        from_field = _safe(lambda: getattr(spec, "from_field", "") or "", "")
        if from_field:
            registry_links.append((prop, from_field))
    registry_links.sort()

    slot_links: list[tuple[str, str]] = []
    # Same evidence resolution as Subfase B: capability_param_map is keyed by
    # CAPABILITY, not by component. Reusing the resolver keeps one definition of
    # "the contract declares this prop" instead of a second, divergent one.
    slot_map = _safe(lambda: slot_prop_to_param(contract, component), {})
    for prop, param in sorted(slot_map.items()):
        if isinstance(param, str) and param and isinstance(prop, str) and prop:
            slot_links.append((prop, param))
    slot_links.sort()

    required = set(requirement.required_props)
    poisoned = _required_props_with_incompatibility(requirement)
    hooks = _hook_candidates(files)

    # ── Candidate assembly ─────────────────────────────────────────────────
    by_key: dict[tuple[str, str, str], CandidateSource] = {}

    def _add(candidate: CandidateSource) -> None:
        key = (candidate.source.kind, candidate.source.ref, candidate.source.selector or "")
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = candidate
        else:
            # Same proven identity: union the evidence, never rank one over the other.
            by_key[key] = replace(
                existing,
                relations=tuple(sorted(set(existing.relations) | set(candidate.relations))),
                prop_field_links=tuple(
                    sorted(set(existing.prop_field_links) | set(candidate.prop_field_links))
                ),
                evidence=tuple(sorted({e.ref + e.detail: e for e in (*existing.evidence, *candidate.evidence)}.values())),
            )

    # (1) hook sources: import proves reachability, call proves fields.
    for symbol, (fields, evidence) in sorted(hooks.items()):
        # Registry + slot links only count when the param/field is actually
        # proven to come out of this source.
        links: list[tuple[str, str]] = []
        relations = ["existing_import", "existing_call"]
        ev = list(evidence)
        for prop, selector in slice_links:
            links.append((prop, selector))
            relations.append("declared_dataslice")
            ev.append(
                EvidenceItem(
                    kind="structural",
                    ref=f"registry:Page.slices[{component}.{prop}]",
                    detail=f"slice selector {selector} (rooted at call field)",
                )
            )
        for prop, from_field in registry_links:
            if rooted_at(from_field, fields):
                links.append((prop, from_field))
                relations.append("existing_registry_binding")
                ev.append(
                    EvidenceItem(
                        kind="structural",
                        ref=f"registry:components.{component}.props.{prop}",
                        detail=f"declared from {from_field}",
                    )
                )
        for prop, param in slot_links:
            if rooted_at(param, fields):
                links.append((prop, param))
                relations.append("contract_slot_prop")
                ev.append(
                    EvidenceItem(
                        kind="structural",
                        ref=f"contract:slots.{component}.{prop}",
                        detail=f"slot param {param} (rooted at call field)",
                    )
                )
        _add(
            CandidateSource(
                source=DataSourceRef(kind="hook", ref=symbol),
                schema=DataSchemaRef(
                    ref=f"hook:{symbol}.result",
                    shape="object" if fields else "unknown",
                    fields=fields,
                ),
                relations=tuple(sorted(set(relations))),
                prop_field_links=tuple(sorted(set(links))),
                evidence=tuple(sorted(ev, key=lambda e: (e.ref, e.detail))),
            )
        )

    # (2) slice sources the registry declares but no call proves. Only emitted
    #     when no hook candidate already covers them.
    if slice_links:
        unproven = tuple(
            (prop, selector)
            for prop, selector in slice_links
            if not any(_merge_into_hook(((prop, selector),), f) for f, _ in hooks.values())
        )
        for prop, selector in unproven:
            _add(
                CandidateSource(
                    source=DataSourceRef(kind="slice", ref=selector, selector=selector),
                    schema=DataSchemaRef(
                        ref=f"registry:Page.slices[{component}.{prop}]",
                        shape="object",
                        fields=(selector,),
                    ),
                    relations=("declared_dataslice",),
                    prop_field_links=((prop, selector),),
                    evidence=(
                        EvidenceItem(
                            kind="structural",
                            ref=f"registry:Page.slices[{component}.{prop}]",
                            detail=f"slice selector {selector} (not provable from any call)",
                        ),
                    ),
                )
            )

    # ── Qualification: source + schema evidence + satisfies the requirement ─
    qualified: list[CandidateSource] = []
    excluded: list[CandidateSource] = []
    for candidate in sorted(
        by_key.values(), key=lambda c: (c.source.kind, c.source.ref, c.source.selector or "")
    ):
        fields = set(candidate.schema.fields)
        if not fields:
            excluded.append(candidate)  # no schema evidence -> not a candidate
            continue
        if not required.issubset({p for p, _ in candidate.prop_field_links}):
            excluded.append(candidate)  # cannot feed every required prop
            continue
        if poisoned & {p for p, _ in candidate.prop_field_links}:
            excluded.append(candidate)  # requirement itself uncertified for that prop
            continue
        # No value-shape check here, on purpose. requirement.expected_shapes
        # describes the shape of the VALUE fed to a prop, while
        # candidate.schema.shape describes the shape of the SOURCE OBJECT
        # exposing fields. Comparing them would repeat the exact cross-stage
        # confusion rejected in Subfase B (param shape vs post-transform prop
        # shape for MetricCard). Value-shape compatibility needs the mapping, so
        # it belongs to schema compatibility in a later subphase, not here.
        qualified.append(candidate)

    return BindingDiscovery(
        target_component=component,
        requirement=requirement,
        candidates=tuple(qualified),
        requirement_incompatibilities=requirement.incompatibilities,
        excluded_candidates=tuple(excluded),
    )


def _required_props_with_incompatibility(requirement: BindingRequirement) -> set[str]:
    """Props this requirement could not certify, per its recorded incompatibilities.

    Incompatibilities are recorded as ``<reason>:<Component>.<prop>``, so a
    conflict on one prop is resolved to that prop instead of poisoning the whole
    requirement. Props with no such record are unaffected.
    """
    poisoned: set[str] = set()
    for entry in requirement.incompatibilities:
        tail = entry.rsplit(":", 1)[-1]
        component, _, prop = tail.partition(".")
        if component == requirement.target_component and prop:
            poisoned.add(prop)
    return poisoned & set(requirement.required_props)