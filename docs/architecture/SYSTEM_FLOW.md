# Agent System — Canonical System Flow

## Status
Canonical flow for the system after F10/F11 cleanup + S1-B Session Lifecycle. There is exactly one materialization route for semantic component operations and exactly one integration route for a Session (`session_merge`).

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
Single FileOps set
   (structural ops + datasource bootstrap + PageCreator converge here)
    ↓
Effective preview — dry_run generation, ZERO writes
   (the exact set a real apply will write)
    ↓
Workspace snapshot recorded (concurrency base)
    ↓
/agent/apply — snapshot re-validation
    ↓
Terminal apply (the ONLY write point)
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

### FileOps (single materialization point)
One terminal write point: the `FileOpApplier` executes the complete set of converged operations — structural ops plus datasource-bootstrap ops plus PageCreator ops. Nothing earlier in the pipeline writes. A `dry_run` run generates the same effective FileOps but performs ZERO writes; the effective preview presented at confirmation is exactly this set, so the preview scope equals the applied scope by construction.

### Verify / Commit
Verify the worktree and commit when required (real applies only).

### Pre-apply concurrency (option C)
Confirmation records a worktree snapshot. `/agent/apply` re-fingerprints the worktree BEFORE applying: if anything changed in between, NOTHING is written (`status=conflict`, `reason=concurrency`, `operations=[]`, phase back to `confirmed`). No merge, no reinterpretation — the user decides.

## Responsibilities

### PageCreator (physical operation)
Not a semantic authority. `create_page_ops` materializes a page CREATE chosen by the user (`create_new`). It verifies the target does not physically exist; a CREATE against an existing target is a CONFLICT (no overwrite, no alternate path, no memory, no heuristics). Deterministic per snapshot. Its ops join the single FileOps set and are re-validated when the set is seeded at apply.

### Orchestrator
Coordinate stages and state transitions. It does not make domain decisions.

## Session Lifecycle (S1-B)

```text
POST /session                     → ACTIVE + unique worktree + branch agent/session-{sid[:8]}
POST /agent/interpret {session_id}→ run bound to Session (run_state.session_id; gate ACTIVE)
POST /agent/confirm {run_id}      → session_id read from run_state; gate ACTIVE
POST /agent/apply {run_id}        → Session lock; re-fingerprint inside lock; 1 commit agent:{run_id}
POST /session/{id}/merge          → single integration: git merge --no-ff -m session:{sid[:8]}
```

A Session is physical continuity + lifecycle (never semantic authority). A Run
is the atomic unit of change. Session states: `ACTIVE → MERGED/CONFLICT/FAILED`
(`CONFLICT` recoverable, `MERGED`/`FAILED` terminal). `MERGE_READY` is derived
at read-time from merge preconditions. One lock per Session (in-process) shared
by apply and merge. Recovery is file + git based (no silent recreate).
See `SESSION_LIFECYCLE.md`.

## Closure baseline (F10/F11)
The system is not in production; no legacy compatibility is preserved. The final contract:

1. Confirmed Plan is the authoritative semantic decision; nothing after confirmation changes it silently.
2. RepositoryValidation returns VALID or CONFLICT. A CONFLICT is reported to the user; validation never replans.
3. The Renderer materializes the confirmed structure; it never interprets intent.
4. FileOps are physical and mechanical; a CREATE writes only a target that does not exist (re-validated when the single FileOps set is seeded at apply).
5. Memory / ConflictResolutionLayer (CRL) provide evidence, never authorization.
6. IdentityResolver contributes proposal/metadata only; it cannot change lifecycle or target.
7. SPLITAnalyzer is analysis/evidence; it never splits automatically.
8. AnchorResolver is physical (anchor-path) resolution, including forced anchors that resolve to a CREATE inside the effective FileOps set; composition behavior belongs to the Renderer.
9. Composition is a renderer contract, not a semantic decision.
10. PageCreator is a physical operation of explicit user choice; its ops join the single FileOps set and are re-validated when the set is seeded at apply.
11. There are no executable legacy routes (BackendRenderer legacy branch removed; no `constraint_graph` flag).
12. There is no backward compatibility for legacy confirm states; `page_creator_ops` originate inside the unified FileOps set at confirm.
13. R8 snapshot-only from `run_id` source, if that invariant still holds, is preserved via tests.
14. Single materialization point: structural ops, datasource bootstrap and PageCreator converge into one FileOps set executed by the terminal `FileOpApplier`. No component writes before that point.
15. `dry_run` is preview-only: it generates the complete effective FileOps with ZERO writes (terminal apply AND semantic-memory persistence are gated).
16. Preview == effective applied scope: confirmation derives the preview from a deterministic dry_run generation; a real apply writes exactly those operations (locked by tests).
17. Pre-apply concurrency (option C): a recorded worktree snapshot is re-checked at apply; any change → `NO WRITE` (`conflict`/`concurrency`), phase back to `confirmed`. No merge, no reinterpretation.
18. Session ≠ semantic authority: it stores lifecycle + physical identity only (no intent/plan/preview/FileOps). `Confirmed Plan` remains the semantic authority per Run.
19. Single integration route (D8a Option A): `POST /session/{id}/merge` runs the only productive `git merge --no-ff`; on conflict it runs `git merge --abort` and marks the Session `CONFLICT` (recoverable). No per-run merge, no second mechanism.
20. Session continuity is physical: one worktree + one branch `agent/session-{sid[:8]}` per Session, commits `agent:{run_id}` max 1 per successful Run; recovery reconstructs from `session_id` + session file + branch + worktree + git log. Never auto-recreate/repair a Session workspace.

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
