"""DELETE RESOLUTION CONTRACT v1.

Ambos resolvedores (structural_completion._resolve_delete_instance
y apply_engine delete loop) DEBEN seguir estas reglas.

No pueden evolucionar de forma independiente.

──┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄
DELETE RESOLUTION CONTRACT v1
──┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄

Input:
    candidates: list[str]    — instancias/archivos disponibles
    instance_hint: str|None  — pista del usuario (filename stem)

Output (exactly one of):
    UNIQUE    → candidates exactly 1 tras filtro por hint
    AMBIGUOUS → >1 candidates no desambiguables
    NO_MATCH  → hint no matchea ningún candidate

Rules (evaluar en orden):

    1. 0 candidates
        → SKIP (no FileOp, log warning)
        Invariant: nunca produce AmbiguousStructuralTargetError

    2. 1 candidate, any hint
        → UNIQUE (no hay ambigüedad que resolver)

    3. >1 candidates, no hint
        → AMBIGUOUS

    4. >1 candidates, hint present:
        4a. hint matchea exactly 1 candidate → UNIQUE
        4b. hint matchea 0 candidates       → NO_MATCH → AMBIGUOUS
        4c. hint matchea >1 candidates      → AMBIGUOUS

Invariant:
    DELETE nunca expande ambigüedad en múltiples FileOps.
    La resolución es siempre single-valued o fail.

──┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄

Implementaciones:
    - structural_completion._resolve_delete_instance  (nivel abstracto)
    - apply_engine delete loop                         (nivel concreto)

El contrato aplica a AMBAS. La prueba de consistencia cruzada
en test_delete_resolution_contract.py verifica que las decisiones
son equivalentes para escenarios equivalentes.
"""

from enum import Enum


class DeleteResolution(Enum):
    SKIP = "skip"
    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"
    NO_MATCH = "no_match"
