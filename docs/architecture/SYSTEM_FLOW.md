# Agent System — Canonical System Flow

## Status
Target canonical flow for cleanup. Existing legacy code is not canonical merely because it remains executable.

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

### Orchestrator
Coordinate stages and state transitions. It does not make domain decisions.

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
