from pydantic import BaseModel
from typing import List, Optional

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

    steps: List[PlanStep]

    metadata: PlanMetadata