FEATURE_FLAGS = {
    "freeze_scaffold": True,
    "skill_ir_output": True,
    "renderer_active": True,
    "executor_dumb": True,
    "constraint_graph": True,             # ConstraintGraph pipeline (composition, CRL, delete) — F5: SPLIT is analysis/evidence only

    # ── Structural path runtime config ──
    "execution_mode": "graphir",          # Execution mode label
    "trace_level": "full",                # Trace verbosity (minimal | normal | full)
    "structural_resolver": True,          # Enable Resolver + Ambiguity Gate (Fase 2)

    # ── Fase 4: Production shell ──
    "verify_worktree": True,              # Post-apply build verification (tsc --noEmit / npm run build)

    # ── PageContextResolver ──
    "page_context_resolver": True,        # Enable page context detection + clarification
}
