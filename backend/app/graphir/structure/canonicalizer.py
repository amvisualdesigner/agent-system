from __future__ import annotations

from dataclasses import dataclass, field

CATEGORY_STRUCTURAL_VALID = "structural_valid"
CATEGORY_STRUCTURAL_UNKNOWN = "structural_unknown"
CATEGORY_NON_STRUCTURAL = "non_structural"


@dataclass
class CanonicalizedOperation:
    operation: dict
    category: str
    reason: str | None = None


@dataclass
class CanonicalizationTrace:
    structural_valid: list[dict] = field(default_factory=list)
    structural_unknown: list[dict] = field(default_factory=list)
    non_structural: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "structural_valid": list(self.structural_valid),
            "structural_unknown": list(self.structural_unknown),
            "non_structural": list(self.non_structural),
        }

    def has_structural(self) -> bool:
        return bool(self.structural_valid or self.structural_unknown)


def _classify(capability: str) -> tuple[str, str | None]:
    """Clasificar una capability en categoría estructural.

    NO consulta registry. Solo usa prefijos de naming.
    Returns:
        (category, reason)
    """
    if capability.startswith("presentation."):
        return CATEGORY_STRUCTURAL_VALID, None
    if capability.startswith("interaction."):
        return CATEGORY_STRUCTURAL_VALID, None
    if capability.startswith("data."):
        return CATEGORY_STRUCTURAL_VALID, None
    if capability == "layout.page":
        return CATEGORY_STRUCTURAL_VALID, None

    if capability.startswith("layout."):
        return CATEGORY_STRUCTURAL_UNKNOWN, "structural_layout_unspecified"
    if capability.startswith("domain."):
        return CATEGORY_NON_STRUCTURAL, "domain_hint"
    if capability.startswith("style."):
        return CATEGORY_NON_STRUCTURAL, "style_hint"

    return CATEGORY_STRUCTURAL_UNKNOWN, "unrecognized_capability"


def canonicalize(operations: list[dict]) -> CanonicalizationTrace:
    """Pure classifier: tag each operation without filtering.

    Args:
        operations: StructuralIR.operations list.

    Returns:
        CanonicalizationTrace with per-category entries.
    """
    trace = CanonicalizationTrace()

    for op in operations:
        action = op.get("action", "")
        target = op.get("target", "")

        if action not in ("CREATE", "MODIFY"):
            continue

        category, reason = _classify(target)

        entry = {
            "capability": target,
            "action": action,
        }
        if reason:
            entry["reason"] = reason

        if category == CATEGORY_STRUCTURAL_VALID:
            trace.structural_valid.append(entry)
        elif category == CATEGORY_STRUCTURAL_UNKNOWN:
            trace.structural_unknown.append(entry)
        else:
            trace.non_structural.append(entry)

    return trace
