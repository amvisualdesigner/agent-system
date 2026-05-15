from pydantic import BaseModel, field_validator

from app.utils.run_id import validate_run_id

class ApplyRequest(BaseModel):
    run_id: str
    plan: dict
    dry_run: bool = False

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, v: str) -> str:
        return validate_run_id(v)