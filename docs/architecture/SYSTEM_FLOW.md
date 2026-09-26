# Agent System — Canonical System Flow

## Status
Canonical flow for the system after F10/F11 cleanup. There is exactly one materialization route for semantic component operations.

## Canonical flow

```text
User request
    ↓
RepositoryContext
    ↓
Interpretation
    ↓
User decision when ambiguous
    ↓
Confirmed Intent
    ↓
Plan Compilation
    ↓
Structural Completion
    ↓
GraphIR
    ↓
Repository Validation
    ↓
Renderer
    ↓
FileOps
    ↓
Verify / Commit
```

## Responsibilities

### RepositoryContext
Provide compact, relevant current-repository facts before interpretation.

### Interpretation
Use the small local model to understand the request. No writes. No silent guessing when materially different choices exist.

### User decision
Resolve semantic ambiguity. The user is the final authority.

### Confirmed Plan
Freeze the semantic decision.

### Structural Completion
Convert the confirmed intention into deterministic structural operations.

### GraphIR
Represent the structural decision without becoming another intent interpreter.

### Repository Validation
Check whether the confirmed plan is still executable. Validation is not replanning.

### Renderer
Materialize the confirmed structure into concrete file operations.

### FileOps
Apply concrete operations.

### Verify / Commit
Verify the worktree and commit when required.

## Responsibilities

### PageCreator (physical operation)
Not a semantic authority. `create_page_ops` materializes a page CREATE chosen by the user (`create_new`). It verifies the target does not physically exist; a CREATE against an existing target is a CONFLICT (no overwrite, no alternate path, no memory, no heuristics). Deterministic per snapshot.

### Orchestrator
Coordinate stages and state transitions. It does not make domain decisions.

## Closure baseline (F10/F11)
The system is not in production; no legacy compatibility is preserved. The final contract:

1. Confirmed Plan is the authoritative semantic decision; nothing after confirmation changes it silently.
2. RepositoryValidation returns VALID or CONFLICT. A CONFLICT is reported to the user; validation never replans.
3. The Renderer materializes the confirmed structure; it never interprets intent.
4. FileOps are physical and mechanical; a CREATE writes only a target that does not exist (PageCreator enforces this at apply time too).
5. Memory / ConflictResolutionLayer (CRL) provide evidence, never authorization.
6. IdentityResolver contributes proposal/metadata only; it cannot change lifecycle or target.
7. SPLITAnalyzer is analysis/evidence; it never splits automatically.
8. AnchorResolver is physical (anchor-path) resolution; composition behavior belongs to the Renderer.
9. Composition is a renderer contract, not a semantic decision.
10. PageCreator is a physical operation of explicit user choice, with physical validation at generation and at apply (agent_apply pre-apply guard).
11. There are no executable legacy routes (BackendRenderer legacy branch removed; no `constraint_graph` flag).
12. There is no backward compatibility for old confirm states (page_creator_ops re-apply removed).
13. R8 snapshot-only from `run_id` source, if that invariant still holds, is preserved via tests.

## Canonical-route rule
There is one semantic route from confirmed intent to applied changes. Helpers may exist outside it, but must not intercept and silently rewrite the plan.

## Architecture maintenance
When this flow changes:
1. update this document;
2. update `DECISION_BOUNDARIES.md`;
3. update `REPOSITORY_KNOWLEDGE.md`;
4. update `CLEANUP_FUNCTIONALITY_MATRIX.md`;
5. update tests;
6. remove obsolete documentation of the old route.
