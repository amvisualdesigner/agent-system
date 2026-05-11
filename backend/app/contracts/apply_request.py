from pydantic import BaseModel

class ApplyRequest(BaseModel):
    run_id: str
    plan: dict