"""Reconciliation — produce SemanticResolution desde frame + SkillIR proposal.

Reglas de resolución (por prioridad):

  1. Frame constraint source="explicit" — override absoluto
     - Si dos explicit conflicts para el mismo param → SemanticConflictError
     - Si explicit vs SkillIR → gana explicit + trace (no error)

  2. Frame constraint source="inferred" — merge si no hay conflicto

  3. SkillIR.params — para params no cubiertos por frame

  4. Intent.params — NO ENTRAN NUNCA. Son legacy, solo para telemetría/auditoría.

NO hay fallback a Intent.params. Si ni frame ni SkillIR lo proponen,
el parámetro no existe en la resolución.
"""

from __future__ import annotations

import logging

from app.contracts.semantic_resolution import (
    SemanticResolution,
    SemanticConflictError,
)

logger = logging.getLogger(__name__)


def reconcile(
    frame_dict: dict | None,
    skill_ir,
) -> SemanticResolution:
    """Produce SemanticResolution desde frame + SkillIR proposal.

    Args:
        frame_dict: Diccionario del StructuredSemanticFrame (o None).
                    Espera claves: "actions", "objects", "constraints",
                    "confidence", "missing_info".
        skill_ir: Objeto SkillIR con contract_id y params.

    Returns:
        SemanticResolution con params limpios y trazabilidad.

    Raises:
        SemanticConflictError: Si hay conflictos irresolubles.
    """
    # Sin frame → fallback directo a SkillIR (backward compat)
    if frame_dict is None:
        return SemanticResolution.from_skillir(skill_ir)

    if not skill_ir.contract_id:
        raise SemanticConflictError(
            "No contract_id available — neither frame nor SkillIR "
            "can determine target contract"
        )

    constraints = frame_dict.get("constraints", [])
    trace: list[str] = []
    params: dict = {}
    provenance: dict[str, str] = {}

    # Separar explicit de inferred
    explicit = [c for c in constraints if c.get("source") == "explicit"]
    inferred = [c for c in constraints if c.get("source") == "inferred"]

    # ── Regla 1: Explicit vs Explicit → HARD FAIL ────────────────
    explicit_map: dict[str, list] = {}
    for c in explicit:
        p = c["param"]
        if p in explicit_map and explicit_map[p] != c["value"]:
            raise SemanticConflictError(
                f"Conflicting explicit constraints for '{p}': "
                f"{explicit_map[p]!r} vs {c['value']!r}. "
                "User cannot specify two different values for the same parameter.",
                param=p,
                user_value=explicit_map[p],
                proposal_value=c["value"],
            )
        explicit_map[p] = c["value"]

    # ── Regla 2: Aplicar explicit → override absoluto ────────────
    for c in explicit:
        p = c["param"]
        params[p] = c["value"]
        provenance[p] = "user_explicit"
        trace.append(f"[explicit] {p}={c['value']!r}")

    # ── Regla 3: Conflict trace: explicit vs SkillIR ──────────────
    for p in explicit_map:
        if p in skill_ir.params and skill_ir.params[p] != params[p]:
            trace.append(
                f"[explicit→override] {p}: SkillIR proposed "
                f"{skill_ir.params[p]!r} — overridden by user_explicit"
            )

    # ── Regla 4: Inferred → merge si no hay conflicto ────────────
    for c in inferred:
        p = c["param"]
        if p not in params:
            params[p] = c["value"]
            provenance[p] = "user_inferred"
            trace.append(f"[inferred] {p}={c['value']!r}")
        elif params[p] != c["value"]:
            trace.append(
                f"[inferred→skip] {p}: inferred {c['value']!r} "
                f"conflicts with existing {params[p]!r} — skipped"
            )

    # ── Regla 5: SkillIR para params no cubiertos ─────────────────
    for k, v in skill_ir.params.items():
        if k not in params:
            params[k] = v
            provenance[k] = "skillir_proposed"
            trace.append(f"[skillir] {k}={v!r}")

    # ── Regla 6: Warnings en trace para acciones sin cobertura ────
    actions = frame_dict.get("actions", [])
    for a in actions:
        verb = a.get("verb", "?")
        obj = a.get("object", "?")
        key = f"{verb} {obj}"
        if key not in str(params):
            trace.append(f"[missing] Action '{key}' has no corresponding param")

    # ── Confianza: mínimo entre frame y SkillIR ──────────────────
    frame_conf = frame_dict.get("confidence", 0.0)
    confidence = (
        min(frame_conf, skill_ir.confidence)
        if skill_ir.confidence > 0
        else frame_conf
    )

    logger.info(
        "Reconciliation: contract=%s | params=%s | provenance=%s",
        skill_ir.contract_id, params, provenance,
    )

    return SemanticResolution(
        contract_id=skill_ir.contract_id,
        contract_version=skill_ir.version,
        params=params,
        param_provenance=provenance,
        confidence=confidence,
        resolution_trace=trace,
    )
