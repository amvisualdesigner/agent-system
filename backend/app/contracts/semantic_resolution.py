from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class SemanticConflictError(ValueError):
    """Raised when reconciliation detects unresolvable semantic conflicts.

    Explicit vs explicit conflicts always raise.
    """
    def __init__(
        self,
        message: str,
        param: str | None = None,
        user_value: Any = None,
        proposal_value: Any = None,
    ):
        self.param = param
        self.user_value = user_value
        self.proposal_value = proposal_value
        super().__init__(message)


@dataclass
class SemanticResolution:
    """Pure semantic resolution — only what the user's language expresses.

    This is the FIRST IR stage: purely linguistic interpretation.
    No contract knowledge, no defaults, no slot mappings.

    Fields:
        semantic_params: params extracted from user language (frame constraints)
        semantic_provenance: provenance per param ("user_explicit", "user_inferred")
        confidence: how well the user's intent was understood
        resolution_trace: audit trail of resolution decisions
    """
    semantic_params: dict[str, Any]
    semantic_provenance: dict[str, str]
    confidence: float
    resolution_trace: list[str] = field(default_factory=list)
