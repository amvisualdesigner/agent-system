FEATURE_FLAGS = {
    "freeze_scaffold": True,
    "skill_ir_output": True,
    "renderer_active": True,
    "executor_dumb": True,
    "constraint_graph": True,             # ConstraintGraph pipeline (composition, CRL, split, delete)

    # ── Structural path runtime config ──
    "execution_mode": "graphir",          # Execution mode label
    "trace_level": "full",                # Trace verbosity (minimal | normal | full)
    "structural_resolver": False,         # Enable Resolver + Ambiguity Gate (Fase 1b)
}
