"""Engine-level errors for structural resolution and targeting."""


class AmbiguousStructuralTargetError(Exception):
    """Raised when the structural target cannot be uniquely resolved.

    The system could not determine which specific UI component instance
    the user intended to target. This MUST be converted to a
    clarification_needed response, NOT a GraphIR crash.
    """

