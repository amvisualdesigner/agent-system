"""Fase 6.1 — Requirement → Discovery → Proposal en la fase pre-confirmación.

Integra la cadena determinista de Fase 6 dentro del pipeline REAL de
`/agent/interpret`, sin crear una segunda pipeline de interpretación ni un
nuevo servicio de resolución: es el mismo patrón de enriquecimiento que
`app.intent.instance_choices` (hechos del repositorio, adjuntos al draft,
nunca elegidos por el LLM).

Reglas duras de esta subfase:

  1. El LLM propone WHAT/WHERE (`verb`, `target_capability`). El DataBinding
     NUNCA procede del LLM: procede de evidencia estructural del repositorio
     (`BindingProposal`) y, cuando hace falta, de la decisión humana en
     `/agent/confirm`.
  2. El registry v4 es evidencia PREVIA, no autoridad post-confirmación: aquí
     solo alimenta `BindingRequirement` y `build_candidate_mappings`.
  3. Sin requisito declarado → sin propuesta. No se fabrica un binding para un
     componente que ninguna evidencia exige alimentar con datos.
  4. El enforcement (auto_unique / needs_choice / unresolved) ocurre en
     `/agent/confirm`; aquí solo se expone la propuesta y se marca qué
     decisiones quedan pendientes.
  5. `remove`/`keep` no materializan componente con datos → no se les exige
     binding (exigirlo impediría eliminar un componente ambiguo).
"""

from __future__ import annotations

import logging
import os

from app.binding.discovery import discover_binding_candidates
from app.binding.mapping import derive_binding_proposal
from app.binding.requirement import build_binding_requirement
from app.intent.models import (
    BindingProposal,
    DataBinding,
    DataMappingEntry,
    DataSchemaRef,
    DataSourceRef,
)

logger = logging.getLogger(__name__)

# Verbos cuya acción MATERIALIZA el componente: solo ellos exigen binding.
BINDING_GATING_VERBS = frozenset({"create", "modify", "transform"})

# Motivos de rechazo del gate de binding en /agent/confirm.
BINDING_UNRESOLVED = "binding_unresolved"
BINDING_CHOICE_PENDING = "binding_choice_pending"
BINDING_NOT_FROM_PROPOSAL = "binding_not_from_proposal"

_SOURCE_SUFFIXES = (".ts", ".tsx")
_SKIP_DIRS = frozenset({"node_modules", ".git", "dist", "build", ".next", "__pycache__"})
_MAX_SOURCE_FILES = 500
_MAX_SOURCE_BYTES = 512 * 1024


def component_for_capability(contract, target_capability: str) -> str | None:
    """component_type exacto que implementa `target_capability`.

    `ast_template["capabilities"]` es {component_type: capability_id}. Si un
    capability id no tiene EXACTAMENTE un componente no se resuelve: elegir uno
    sería inventar el target del binding.
    """
    if contract is None or not target_capability:
        return None
    caps = (getattr(contract, "ast_template", None) or {}).get("capabilities") or {}
    matches = sorted(c for c, cap in caps.items() if cap == target_capability)
    return matches[0] if len(matches) == 1 else None


def read_source_files(workspace: str | None) -> dict[str, str]:
    """Contenidos .ts/.tsx del worktree para discovery (lectura, no análisis).

    Mismo alcance que `extract_signatures`: recorre el workspace completo.
    Discovery es puro; nunca abre ficheros por su cuenta.
    """
    if not workspace or not os.path.isdir(workspace):
        return {}
    out: dict[str, str] = {}
    for root, dirs, files in os.walk(workspace):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in sorted(files):
            if not name.endswith(_SOURCE_SUFFIXES):
                continue
            if len(out) >= _MAX_SOURCE_FILES:
                return out
            path = os.path.join(root, name)
            try:
                if os.path.getsize(path) > _MAX_SOURCE_BYTES:
                    continue
                with open(path, encoding="utf-8", errors="replace") as fh:
                    out[os.path.relpath(path, workspace)] = fh.read()
            except OSError:
                continue
    return out


def build_action_binding_proposal(
    target_component: str,
    *,
    contract=None,
    signature: dict | None = None,
    v4_bindings=None,
    page_data_source=None,
    target_file_contents=None,
) -> BindingProposal | None:
    """Requirement → Discovery → Proposal para UN componente consumidor.

    Devuelve `None` cuando la evidencia no llega a formular un requisito
    (sin props de datos declaradas y sin incompatibilidades): no hay nada que
    enlazar y fabricar una propuesta sería inventar una conexión.
    """
    requirement = build_binding_requirement(
        target_component,
        contract=contract,
        signature=signature,
        v4_bindings=v4_bindings,
    )
    if not requirement.required_props and not requirement.incompatibilities:
        return None

    discovery = discover_binding_candidates(
        requirement,
        contract=contract,
        v4_bindings=v4_bindings,
        page_data_source=page_data_source,
        target_file_contents=target_file_contents,
    )
    return derive_binding_proposal(discovery, v4_bindings=v4_bindings, contract=contract)


def enrich_draft_binding_proposals(
    draft_dict: dict,
    *,
    workspace: str | None,
    contract=None,
) -> list[dict]:
    """Adjunta `binding_proposal` a cada acción que materializa componente.

    Mutates `draft_dict` (mismo contrato que `enrich_draft_instance_choices`).
    Devuelve el resumen de propuestas; también queda en
    `draft_dict["binding_proposals"]` y las no resueltas en
    `draft_dict["binding_decisions_pending"]` para que `/agent/confirm` pueda
    exigir la decisión humana sin redescubrir nada.
    """
    actions = draft_dict.get("proposed_actions") or []
    gating = [
        a for a in actions
        if isinstance(a, dict)
        and (a.get("verb") or "").lower() in BINDING_GATING_VERBS
        and a.get("target_capability")
    ]
    if not gating:
        return []

    if contract is None:
        from app.contracts.skill_registry import get_contract

        contract = get_contract(
            draft_dict.get("contract_id", ""),
            int(draft_dict.get("contract_version", 1) or 1),
        )
    if contract is None:
        logger.info("binding proposal: contract not resolvable; skipping")
        return []

    inverse: dict[str, list[str]] = {}
    for comp, cap in ((contract.ast_template or {}).get("capabilities") or {}).items():
        inverse.setdefault(cap, []).append(comp)

    try:
        from app.signature.prop_mapper import load_page_data_source, load_v4_bindings
        from app.signature.extractor import extract_signatures

        v4_bindings = load_v4_bindings()
        page_data_source = load_page_data_source()
        signatures = extract_signatures(workspace) if workspace else {}
    except Exception as e:
        logger.warning("binding proposal: evidence loading failed: %s", e)
        return []

    target_file_contents = read_source_files(workspace)
    summary: list[dict] = []

    for action in gating:
        comp_candidates = sorted(inverse.get(action["target_capability"]) or [])
        if len(comp_candidates) != 1:
            if len(comp_candidates) > 1:
                logger.info(
                    "binding proposal: %s maps to %s; ambiguous target, skipping",
                    action["target_capability"], comp_candidates,
                )
            continue
        component = comp_candidates[0]
        try:
            proposal = build_action_binding_proposal(
                component,
                contract=contract,
                signature=signatures.get(component),
                v4_bindings=v4_bindings,
                page_data_source=page_data_source,
                target_file_contents=target_file_contents,
            )
        except Exception as e:
            logger.warning(
                "binding proposal: %s failed: %s", component, e
            )
            continue
        if proposal is None:
            continue

        action["binding_proposal"] = proposal.to_dict()
        summary.append({
            "target_capability": action["target_capability"],
            "target_component": component,
            "verb": (action.get("verb") or "").lower(),
            "status": proposal.status,
        })

    if summary:
        draft_dict["binding_proposals"] = summary
        draft_dict["binding_decisions_pending"] = [
            p for p in summary if p["status"] != "auto_unique"
        ]
    return summary


# ── Gate de confirmación (Fase 6.1) ───────────────────────────────────────
#
# El binding que cruza /agent/confirm se CONSTRUYE desde la propuesta; el
# payload del cliente solo SELECCIONA entre candidatas ya probadas. Así el
# LLM (o cualquier cliente) puede aportar la decisión, nunca la autoridad:
# un `source.ref` o un `from_field` inexistentes se rechazan.


def _entry_matches(entry: DataMappingEntry, candidate_mapping: dict) -> bool:
    return (
        entry.prop == candidate_mapping.get("prop")
        and entry.from_field == candidate_mapping.get("value_path")
        and (entry.transform or "identity")
        == (candidate_mapping.get("transform") or "identity")
    )


def _find_candidate(proposal: BindingProposal, submitted: DataBinding) -> dict | None:
    src = submitted.source
    for candidate in proposal.provenance.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("kind") != src.kind or candidate.get("ref") != src.ref:
            continue
        if (candidate.get("selector") or None) != (src.selector or None):
            continue
        return candidate
    return None


def resolve_confirmed_binding(
    submitted: dict | None,
    proposal_dict: dict | None,
) -> tuple[DataBinding | None, str | None]:
    """Cierra Requirement → Proposal → decisión humana en el punto de confirm.

    Returns:
        (binding, None)          → binding confirmado procedente de la propuesta
        (None, motivo)           → rechazo; `motivo` es BINDING_* de arriba
        (None, None)             → la acción no exige binding (sin propuesta)

    Reglas:
      * auto_unique   → se materializa la propuesta; un payload distinto es un
                        binding arbitrario.
      * needs_choice  → exige selección humana de UNA candidata probada; la
                        fuente y los `from_field` salen de la evidencia.
      * unresolved    → clarification (nunca fallback al registry).
      * sin propuesta → la acción no tiene requisito declarado: un binding
                        enviado igualmente se rechaza (no hay propuesta que lo
                        avale).
    """
    if not isinstance(submitted, dict):
        submitted = None
    proposal = BindingProposal.from_dict(proposal_dict)

    if proposal is None:
        if submitted is not None:
            return None, BINDING_NOT_FROM_PROPOSAL
        return None, None

    if proposal.status == "auto_unique":
        expected = proposal.to_binding()
        if expected is None:
            return None, BINDING_UNRESOLVED
        if submitted is None:
            return expected, None
        got = DataBinding.from_dict(submitted)
        if got is None or got.to_dict() != expected.to_dict():
            return None, BINDING_NOT_FROM_PROPOSAL
        return expected, None

    if proposal.status == "needs_choice":
        if submitted is None:
            return None, BINDING_CHOICE_PENDING
        got = DataBinding.from_dict(submitted)
        if got is None:
            return None, BINDING_NOT_FROM_PROPOSAL
        candidate = _find_candidate(proposal, got)
        if candidate is None:
            return None, BINDING_NOT_FROM_PROPOSAL
        candidate_mappings = [
            m for m in (candidate.get("mappings") or []) if isinstance(m, dict)
        ]
        candidate_props = {m.get("prop") for m in candidate_mappings}
        if not candidate_props or {e.prop for e in got.mapping} != candidate_props:
            return None, BINDING_NOT_FROM_PROPOSAL
        for entry in got.mapping:
            if not any(_entry_matches(entry, m) for m in candidate_mappings):
                return None, BINDING_NOT_FROM_PROPOSAL
        # El binding confirmado se reconstruye con la evidencia de la
        # candidata elegida: la selección humana aporta la candidata, no la
        # autoridad del source ni de los from_field.
        return (
            DataBinding(
                source=DataSourceRef(
                    kind=candidate.get("kind", ""),
                    ref=candidate.get("ref", ""),
                    selector=candidate.get("selector"),
                ),
                schema=DataSchemaRef.from_dict(candidate.get("schema")),
                mapping=tuple(
                    DataMappingEntry(
                        prop=e.prop,
                        from_field=e.from_field,
                        transform=e.transform or "identity",
                    )
                    for e in got.mapping
                ),
            ),
            None,
        )

    # Cualquier otro status (o status desconocido) es equivalente a
    # "sin resolver": clarification, sin fallback al registry.
    return None, BINDING_UNRESOLVED
