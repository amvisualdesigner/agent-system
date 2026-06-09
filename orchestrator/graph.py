from langgraph.graph import StateGraph, END
from state import AgentState
from nodes.interpret import interpret_node
from nodes.confirm import confirm_node
from nodes.validate_plan import validate_plan_node
from nodes.call_apply import call_apply_node
from nodes.return_result import return_result_node


def entry_router(state: AgentState) -> str:
    """Conditional entry: dispatch to the right node based on phase."""
    start = state.get("start_node") or state.get("phase", "planning")
    if start == "confirm":
        return "confirm"
    if start == "apply":
        return "call_apply"
    return "interpret"


def router(state: AgentState) -> str:
    return state.get("_next_node", "return_result")


graph = StateGraph(AgentState)

graph.add_node("interpret", interpret_node)
graph.add_node("confirm", confirm_node)
graph.add_node("validate_plan", validate_plan_node)
graph.add_node("call_apply", call_apply_node)
graph.add_node("return_result", return_result_node)

graph.set_conditional_entry_point(
    entry_router,
    {
        "interpret": "interpret",
        "confirm": "confirm",
        "call_apply": "call_apply",
    },
)

# interpret: may retry on clarification, otherwise return_result
graph.add_conditional_edges(
    "interpret",
    router,
    {
        "interpret": "interpret",
        "return_result": "return_result",
    },
)

# confirm → validate_plan (or return_result when skipped)
graph.add_conditional_edges(
    "confirm",
    router,
    {
        "validate_plan": "validate_plan",
        "return_result": "return_result",
    },
)

# validate_plan: always goes to return_result (awaiting_apply or error)
graph.add_conditional_edges(
    "validate_plan",
    router,
    {
        "return_result": "return_result",
    },
)

# call_apply → return_result → END
graph.add_edge("call_apply", "return_result")
graph.add_edge("return_result", END)

compiled_graph = graph.compile()
