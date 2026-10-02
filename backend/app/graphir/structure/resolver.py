from __future__ import annotations

from typing import TYPE_CHECKING

from app.engine.structural_index import StructuralIndex
from app.engine.state_adapter import ComponentInstanceInfo
from app.engine.errors import AmbiguousStructuralTargetError
from app.graphir.intent import is_capability_metadata
from app.graphir.structure.models import StructuralResolution
from app.graphir.structure.registry import StructuralRegistry

if TYPE_CHECKING:
    from app.engine.structural_completion import (
        CREATE, MODIFY, DELETE, KEEP, StructuralIR,
    )

GLOBAL_THRESHOLD: float = 0.5


def _select_instance(
    instances: list[ComponentInstanceInfo],
    instance_hint: str | None = None,
) -> str | None:
    """Strict deterministic instance selection.

    0 instances → return None (caller creates new)
    explicit instance_hint → the single instance it names (no fuzzy)
    1 instance → return its instance_id
    N instances → return min lexicographic instance_id

    F11 contract: PROPOSAL/PROPERTE-level only. This selection feeds
    `capability_to_path` (used for RepositoryValidation R1c projection and
    GraphIR builder metadata) and never reaches materialization on its own:
    RepositoryValidation R-b (MODIFY with N>1 and no explicit selection) raises
    CONFLICT before any file is written. A silent N→1 pick must never become a
    rendered target.
    """
    if not instances:
        return None
    if instance_hint:
        matched = [i for i in instances if _instance_matches(i, instance_hint)]
        if len(matched) == 1:
            return matched[0].instance_id
        if len(matched) > 1:
            raise AmbiguousStructuralTargetError(
                f"instance_hint '{instance_hint}' matches multiple instances for "
                f"'{instances[0].capability}'. Select a single instance."
            )
    return min(info.instance_id for info in instances)


def _instance_matches(inst: ComponentInstanceInfo, hint: str) -> bool:
    """Exact identity match for a human-selected instance (no fuzzy)."""
    if not hint:
        return False
    needle = hint.strip().lower()
    if not needle:
        return False
    if (inst.slot_id or "").lower() == needle:
        return True
    if str(inst.instance_id).lower() == needle:
        return True
    if inst.path.lower() == needle:
        return True
    if inst.file_path and inst.file_path.lower() == needle:
        return True
    return False


def resolve(
    structural_ir: StructuralIR,
    registry: StructuralRegistry,
    structural_index: StructuralIndex | None = None,
) -> StructuralResolution:
    """Resolver estructural con instance selection.

    Para cada operación CREATE/MODIFY en StructuralIR:
      1. Metadata (non_structural) → skip
      2. Busca candidatos en el registry (construido desde el contrato)
      3. Selecciona instance_id para cada capability:
         - Si existe en StructuralIndex → _select_instance() determinista
         - Si es CREATE sin instancias → "0"
      4. Resuelve path:
         - Si hay candidatos en registry → usa component_instance_path
         - Si no hay candidatos → fallback por action type via StructuralIndex

    Siempre retorna un StructuralResolution fully resolved.
    NO retorna clarification_needed — sistema es determinista.

    Returns:
        StructuralResolution con capability_to_path e instance_mapping.
    """
    contract_id = structural_ir.contract_id
    capability_to_path: dict[str, str] = {}
    instance_mapping: dict[str, str] = {}
    trace: list[str] = []
    total_score = 0.0
    resolved_count = 0
    metadata_count = 0
    unknown_count = 0

    for op in structural_ir.operations:
        action: str = op["action"]
        target: str = op["target"]

        if action not in ("CREATE", "MODIFY"):
            continue

        if is_capability_metadata(target):
            metadata_count += 1
            trace.append(f"op:{action}:{target} → non_structural (skipped)")
            continue

        # ── Determine instance_id ──
        instance_id = None
        selected_inst = None
        instance_hint = op.get("instance_hint")
        if structural_index is not None:
            instances = structural_index.get_instances(target)
            instance_id = _select_instance(instances, instance_hint)
            if instance_hint:
                matched = [i for i in instances if _instance_matches(i, instance_hint)]
                if len(matched) == 1:
                    selected_inst = matched[0]

        if instance_id is None:
            instance_id = "0"

        instance_mapping[target] = instance_id

        # ── Determine path ──
        candidates = registry.resolve_candidates(target)

        # Human-selected instance IS the physical target (exact, no fuzzy).
        if selected_inst is not None and selected_inst.file_path:
            capability_to_path[target] = selected_inst.file_path
            total_score += 1.0
            resolved_count += 1
            trace.append(
                f"op:{action}:{target} → {selected_inst.file_path} "
                f"(instance={instance_id}, instance_hint={instance_hint})"
            )
            continue

        if not candidates:
            if structural_index is not None:
                from app.engine.structural_completion import CREATE as _CREATE

                if action == _CREATE:
                    path = StructuralIndex.derive_create_path(
                        structural_ir.contract_id, target,
                    )
                    if path:
                        capability_to_path[target] = path
                        total_score += 1.0
                        resolved_count += 1
                        trace.append(
                            f"op:{action}:{target} → {path} (instance={instance_id}) "
                            f"(derived via StructuralIndex.derive_create_path)"
                        )
                        continue
                else:
                    if structural_index.exists(target):
                        path = structural_index.resolve_path(target)
                        if path:
                            capability_to_path[target] = path
                            total_score += 1.0
                            resolved_count += 1
                            trace.append(
                                f"op:{action}:{target} → {path} (instance={instance_id}) "
                                f"(resolved via StructuralIndex)"
                            )
                            continue

            unknown_count += 1
            trace.append(
                f"op:{action}:{target} → no structural target in registry "
                f"(structural_index fallback also failed)"
            )
            continue

        if len(candidates) > 1:
            paths = [c.component_instance_path for c in candidates]
            raise AmbiguousStructuralTargetError(
                f"Multiple candidates for '{target}': {', '.join(paths)}. "
                f"StructuralResolver requires unambiguous targets. "
                f"Provide an instance_hint to disambiguate."
            )

        best = candidates[0]
        score = 1.0
        capability_to_path[target] = best.component_instance_path
        total_score += score
        resolved_count += 1
        trace.append(
            f"op:{action}:{target} → {best.component_instance_path} "
            f"(instance={instance_id}, score={score:.2f})"
        )

    if resolved_count == 0:
        if metadata_count > 0 and unknown_count == 0:
            reason = "no_structural_intents"
        elif unknown_count > 0:
            reason = "no_structural_targets"
        else:
            reason = "no_resolvable_operations"
        return StructuralResolution(
            capability_to_path={},
            instance_mapping=instance_mapping,
            confidence=1.0,
            trace=trace,
            reason=reason,
        )

    avg_confidence = total_score / resolved_count

    return StructuralResolution(
        capability_to_path=capability_to_path,
        instance_mapping=instance_mapping,
        confidence=avg_confidence,
        trace=trace,
        reason=None,
    )
