from typing import TypedDict, Optional, List, Dict, Any


class TraceEntry(TypedDict):
    node: str
    input: Dict[str, Any]
    output: Dict[str, Any]
    latency_ms: int


class AgentState(TypedDict):
    task: str
    run_id: Optional[str]
    plan: Optional[Dict[str, Any]]
    execution: Optional[Dict[str, Any]]
    run_details: Optional[Dict[str, Any]]
    error: Optional[str]
    retry_count: int
    trace: List[TraceEntry]
    phase: str
    cancelled: bool
    backend_run_id: Optional[str]
    _next_node: Optional[str]
