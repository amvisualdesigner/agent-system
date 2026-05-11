from pydantic import BaseModel
from typing import List, Optional


class PlanRequest(BaseModel):
    task: str
    scope: List[str] = []
    constraints: List[str] = []
    workspace: Optional[str] = None