from pydantic import BaseModel
from typing import List, Optional


class PlanRequest(BaseModel):
    task: str
    run_id: Optional[str] = None
    scope: List[str] = []
    constraints: List[str] = []
    workspace: Optional[str] = None