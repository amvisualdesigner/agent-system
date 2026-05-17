"""Contract resolution result type."""

from dataclasses import dataclass


@dataclass
class ContractResolutionResult:
    ok: bool
    ast: dict | None = None
    reason: str | None = None
