from __future__ import annotations

from dataclasses import dataclass, field, asdict


SKILL_IR_SCHEMA = {
    "type": "object",
    "required": ["contract_id", "confidence"],
    "properties": {
        "contract_id": {
            "type": ["string", "null"],
            "description": "Skill contract ID. Null if no contract confidently matches.",
        },
        "version": {
            "type": "integer",
            "minimum": 1,
            "description": "Version of the selected contract.",
        },
        "params": {
            "type": "object",
            "additionalProperties": True,
            "description": "Parameters matching the contract's input_schema.",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Confidence that the selected contract matches the task.",
        },
    },
}

DEFAULT_THRESHOLD = 0.5


@dataclass
class SkillIR:
    contract_id: str | None
    confidence: float
    params: dict = field(default_factory=dict)
    version: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SkillIR:
        return cls(
            contract_id=data.get("contract_id"),
            version=data.get("version", 1),
            params=data.get("params", {}),
            confidence=data.get("confidence", 0.0),
        )

    def is_valid(self) -> bool:
        if self.contract_id is not None and not isinstance(self.contract_id, str):
            return False
        if not isinstance(self.confidence, (int, float)):
            return False
        if self.confidence < 0.0 or self.confidence > 1.0:
            return False
        if not isinstance(self.params, dict):
            return False
        if not isinstance(self.version, int) or self.version < 1:
            return False
        return True

    def threshold(self, per_contract: dict[str, float] | None = None) -> float:
        if per_contract and self.contract_id and self.contract_id in per_contract:
            return per_contract[self.contract_id]
        return DEFAULT_THRESHOLD

    def should_execute(self, per_contract_thresholds: dict[str, float] | None = None) -> tuple[bool, str]:
        if self.contract_id is None:
            return False, "no_contract"
        if not self.is_valid():
            return False, "invalid_skill_ir"
        th = self.threshold(per_contract_thresholds)
        if self.confidence < th:
            return False, "low_confidence"
        return True, "ok"
