from fastapi import FastAPI
from pydantic import BaseModel

from app.api.agent_plan import router as agent_plan_router
from app.api.agent_apply import router as agent_apply
from app.utils.state import read_state

# -------- CONFIGURACIÓN --------
app = FastAPI()
app.include_router(agent_plan_router)
app.include_router(agent_apply)

# -------- MODELOS --------
class RepoFile(BaseModel):
    path: str
    content: str

# -------- ENDPOINTS --------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/agent/latest")
def latest():
    state = read_state()

    if not state:
        return {"error": "no runs yet"}

    return state