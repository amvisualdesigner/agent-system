from langgraph.graph import StateGraph, END
from state import AgentState
from nodes.call_plan import call_plan_node
from nodes.validate_plan import validate_plan_node
from nodes.call_apply import call_apply_node
from nodes.return_result import return_result_node


def router(state: AgentState) -> str:
    return state.get("_next_node", "return_result")


graph = StateGraph(AgentState)

graph.add_node("call_plan", call_plan_node)
graph.add_node("validate_plan", validate_plan_node)
graph.add_node("call_apply", call_apply_node)
graph.add_node("return_result", return_result_node)

graph.set_entry_point("call_plan")

graph.add_edge("call_plan", "validate_plan")

graph.add_conditional_edges(
    "validate_plan",
    router,
    {
        "call_plan": "call_plan",
        "call_apply": "call_apply",
        "return_result": "return_result",
    },
)

graph.add_edge("call_apply", "return_result")
graph.add_edge("return_result", END)

compiled_graph = graph.compile()
