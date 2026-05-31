from typing import TypedDict, Optional, List, Dict, Any


class TraceEntry(TypedDict):
    node: str
    input: Dict[str, Any]
    output: Dict[str, Any]
    latency_ms: int


class AgentState(TypedDict):
    task: str
    run_id: str
    plan: Optional[Dict[str, Any]]
    execution: Optional[Dict[str, Any]]
    run_details: Optional[Dict[str, Any]]
    error: Optional[str]
    retry_count: int
    trace: List[TraceEntry]
    phase: str
    cancelled: bool
    backend_run_id: Optional[str]
    planner_meta: Optional[Dict[str, Any]]
    _next_node: Optional[str]
    # UI-3: interpret → confirm → apply flow
    start_node: Optional[str]
    interpretation: Optional[Dict[str, Any]]
    confirmed_intent: Optional[Dict[str, Any]]
    plan_preview: Optional[Dict[str, Any]]
