from typing import Optional

from pydantic import BaseModel


class ApplyRequest(BaseModel):
    run_id: Optional[str] = None
    plan: dict | None = None
    dry_run: bool = False