from pydantic import BaseModel

class ApplyRequest(BaseModel):
    run_id: str
    plan: dict
    dry_run: bool = False