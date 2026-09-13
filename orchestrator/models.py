from pydantic import BaseModel, field_validator
from typing import Optional, Any, Dict, List
from datetime import datetime, timezone

from run_id import validate_run_id


class RunRequest(BaseModel):
    task: str


class RunResponse(BaseModel):
    run_id: str

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, v: str) -> str:
        return validate_run_id(v)


class ConfirmRequest(BaseModel):
    contract_id: str = ""
    contract_version: int = 1
    actions: List[Dict[str, Any]] = []
    params: Dict[str, Any] = {}
    user_message: str = ""
    page_context_choice: str | None = None


class ApplyRequest(BaseModel):
    dry_run: bool = False
    confirmed_deletions: list[str] = []


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
    context: Optional[Dict[str, Any]] = None
    meta: Optional[Dict[str, Any]] = None
    status: str
    error: Optional[str] = None

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, v: str) -> str:
        return validate_run_id(v)
