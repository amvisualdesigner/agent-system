from pydantic import BaseModel
from typing import Optional, Any, Dict, List
from datetime import datetime, timezone


class RunRequest(BaseModel):
    task: str


class RunResponse(BaseModel):
    run_id: str


class SSEEvent(BaseModel):
    type: str
    node: Optional[str] = None
    phase: str
    run_id: Optional[str] = None
    data: Optional[Any] = None
    error: Optional[str] = None
    timestamp: str = ""

    def model_dump(self, *args, **kwargs):
        d = super().model_dump(*args, **kwargs)
        if not d["timestamp"]:
            d["timestamp"] = datetime.now(timezone.utc).isoformat()
        return d


class RunResult(BaseModel):
    run_id: str
    plan: Optional[Dict[str, Any]] = None
    execution: Optional[Dict[str, Any]] = None
    diff: Optional[str] = None
    files: Optional[List[str]] = None
    status: str
    error: Optional[str] = None
