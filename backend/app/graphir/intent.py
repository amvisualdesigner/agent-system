"""IntentPlan — semantic bridge between natural language and GraphIR.

The AST is not eliminated. Its bridge function (natural language → structure)
is absorbed into IntentPlan, which is the formal, typed, LLM-friendly
semantic layer.

IntentPlan is produced by the LLM, consumed by the GraphIR builder,
and discarded after GraphIR construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class IntentType(Enum):
    """Core semantic intent types — canonical and strict.

    These are the BUILT-IN types that the base system supports.
    They are validated at parse time. If the LLM outputs a type
    not in this enum AND not in IntentExtensionRegistry, it is
    rejected.

    The registry is extensible via IntentExtensionRegistry
    so new types can be added without redeploying the core.
    """
    PAGE = "Page"
    KPIGROUP = "KPIGroup"
    CHART = "Chart"
    DATATABLE = "DataTable"
    FILTERPANEL = "FilterPanel"
    EMBED = "Embed"


_INTENT_TO_GRAPHIR_TYPE = {
    IntentType.PAGE: "Page",
    IntentType.KPIGROUP: "KpiRow",
    IntentType.CHART: "Timeseries",
    IntentType.DATATABLE: "AnalyticsTable",
    IntentType.FILTERPANEL: "FilterPanel",
    IntentType.EMBED: "Embed",
}

_INTENT_TO_EDGE_ROLE = {
    IntentType.KPIGROUP: "PRIMARY",
    IntentType.CHART: "SUPPORTING",
    IntentType.DATATABLE: "SUPPORTING",
    IntentType.FILTERPANEL: "SUPPORTING",
    IntentType.EMBED: "CONTAINS",
}


class IntentExtensionRegistry:
    """Dynamic registry for semantic intent types.

    Allows registering new intent types at runtime without
    modifying IntentType. Each extension defines:
      - name: unique identifier
      - graphir_type: the GraphIRNode.type it maps to
      - edge_role: default EdgeRole name for edges targeting this type
      - params_schema: optional dict for params validation

    Registered extensions are validated alongside core IntentType
    during IntentPlan validation.

    This prevents:
      - Redeploy requirement for every new component type
      - LLM drift causing hard failures
      - Forking IntentType for domain-specific concepts
    """
    _extensions: dict[str, dict] = {}

    @classmethod
    def register(cls, name: str, config: dict) -> None:
        required = {"graphir_type", "edge_role"}
        missing = required - set(config.keys())
        if missing:
            raise ValueError(
                f"IntentExtension '{name}' missing required keys: {missing}"
            )
        if config["edge_role"] not in ("CONTAINS", "PRIMARY", "SUPPORTING"):
            raise ValueError(
                f"IntentExtension '{name}': edge_role must be one of "
                f"CONTAINS/PRIMARY/SUPPORTING, got '{config['edge_role']}'"
            )
        cls._extensions[name] = dict(config)

    @classmethod
    def unregister(cls, name: str) -> None:
        cls._extensions.pop(name, None)

    @classmethod
    def is_valid(cls, name: str) -> bool:
        return name in IntentType.__members__ or name in cls._extensions

    @classmethod
    def resolve_graphir_type(cls, name: str) -> str | None:
        if name in IntentType.__members__:
            return _INTENT_TO_GRAPHIR_TYPE.get(IntentType[name])
        ext = cls._extensions.get(name)
        return ext.get("graphir_type") if ext else None

    @classmethod
    def resolve_edge_role(cls, name: str) -> str | None:
        if name in IntentType.__members__:
            return _INTENT_TO_EDGE_ROLE.get(IntentType[name])
        ext = cls._extensions.get(name)
        return ext.get("edge_role") if ext else None

    @classmethod
    def list_extensions(cls) -> dict[str, dict]:
        return dict(cls._extensions)

    @classmethod
    def clear(cls) -> None:
        cls._extensions.clear()


@dataclass(frozen=True)
class IntentNode:
    """A single semantic intent from natural language.

    'type' MUST be valid per IntentType + IntentExtensionRegistry.
    'params' contains business-level parameters ONLY.
    No layout keys. No rendering keys. No framework references.
    """
    type: str
    params: dict[str, Any] = field(default_factory=dict)
    description: str | None = None


@dataclass(frozen=True)
class IntentPlan:
    """The ONLY bridge between natural language and GraphIR.

    Produced by the LLM. Consumed by the GraphIR builder.
    Discarded after GraphIR construction.

    RULES:
    - NO layout decisions
    - NO edges
    - NO rendering details
    - NO framework references (React, Qwik, etc.)
    - ONLY semantic intent
    """
    contract_id: str | None
    version: int
    confidence: float
    intents: list[IntentNode]
    params: dict[str, Any]

    @classmethod
    def validate(cls, plan: "IntentPlan") -> None:
        """Validate at construction boundary.

        Rejects:
          - unknown intent types (not in IntentType or IntentExtensionRegistry)
        """
        if not plan.intents:
            raise ValueError("IntentPlan must have at least one IntentNode")

        for i, intent in enumerate(plan.intents):
            if not IntentExtensionRegistry.is_valid(intent.type):
                raise ValueError(
                    f"IntentPlan.intents[{i}]: unknown intent type "
                    f"'{intent.type}'. Must be in IntentType or "
                    f"registered in IntentExtensionRegistry."
                )

        if plan.confidence < 0.0 or plan.confidence > 1.0:
            raise ValueError(
                f"IntentPlan.confidence must be in [0, 1], got {plan.confidence}"
            )
