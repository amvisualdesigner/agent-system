"""Reconciliation — produce SemanticResolution desde frame.

Resolución puramente semántica: extrae lo que el usuario DICE
del lenguaje natural. NO incluye params de contrato ni SkillIR.

Reglas de resolución (por prioridad):

  1. Frame constraint source="explicit" — override absoluto
     - Si dos explicit conflicts para el mismo param → SemanticConflictError

  2. Frame constraint source="inferred" — merge si no hay conflicto

  3. SkillIR.params NO entran aquí. Son competencia de ContractResolution.

  4. Intent.params — NO ENTRAN NUNCA. Son legacy.
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
    skill_ir=None,
) -> SemanticResolution:
    """Produce SemanticResolution puramente desde el frame semántico.

    Args:
        frame_dict: Diccionario del StructuredSemanticFrame (o None).
                    Espera claves: "actions", "objects", "constraints",
                    "confidence", "missing_info".
        skill_ir: No usado para params. Solo se acepta para mantener
                  firma compatible (legacy). Los params vienen del frame.

    Returns:
        SemanticResolution con solo params lingüísticos del usuario.

    Raises:
        SemanticConflictError: Si hay conflictos irresolubles.
    """
    # Sin frame → resolución vacía (sin params semánticos)
    if frame_dict is None:
        return SemanticResolution(
            semantic_params={},
            semantic_provenance={},
            confidence=0.0,
            resolution_trace=["No semantic frame — empty resolution"],
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

    # ── Regla 3: Inferred → merge si no hay conflicto ────────────
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

    # ── Regla 4: Observaciones para acciones sin cobertura ──────
    actions = frame_dict.get("actions", [])
    for a in actions:
        verb = a.get("verb", "?")
        obj = a.get("object", "?")
        key = f"{verb} {obj}"
        if key not in str(params):
            trace.append(f"[action] '{key}' has no corresponding param")

    # ── Confianza: desde frame ──────────────────────────────────
    frame_conf = frame_dict.get("confidence", 0.0)
    if skill_ir is not None and hasattr(skill_ir, 'confidence') and skill_ir.confidence > 0:
        confidence = min(frame_conf, skill_ir.confidence)
    else:
        confidence = frame_conf

    logger.info(
        "Reconciliation: semantic_params=%s | provenance=%s",
        params, provenance,
    )

    return SemanticResolution(
        semantic_params=params,
        semantic_provenance=provenance,
        confidence=confidence,
        resolution_trace=trace,
    )
