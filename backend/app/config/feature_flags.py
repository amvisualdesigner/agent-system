FEATURE_FLAGS = {
    "freeze_scaffold": True,
    "skill_ir_output": True,
    "renderer_active": True,
    "executor_dumb": True,
    "legacy_removed": True,
    "constraint_graph": True,             # RepositoryAwareRenderer active (composition, CRL, split, delete)
    "constraint_graph_mvp": False,        # Enable heuristic-only MVP mode
    "constraint_graph_line_range": False, # Enable line-range merge (Phase 5)
    "constraint_graph_shadow": False,     # Shadow-mode disabled — constraint pipeline is primary
}
