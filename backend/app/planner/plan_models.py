from pydantic import BaseModel, field_validator
from typing import List, Optional

from app.utils.run_id import validate_run_id

class PlanStep(BaseModel):
    id: str
    path: str

    action: str

    intent: str

    constraints: List[str] = []

    proposed_content: Optional[str] = None


class PlanMetadata(BaseModel):
    planner_model: str
    confidence: float = 0.0
    warnings: List[str] = []


class Plan(BaseModel):
    run_id: str
    task: str

    status: str = "draft"

    actions: List[dict]

    metadata: PlanMetadata

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, v: str) -> str:
        return validate_run_id(v)