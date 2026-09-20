# Agent System — Cleanup Plan

## Purpose
Executable cleanup contract for the Agent System. Reduce competing execution paths before adding new architecture.

## Rules
1. Inspect actual code and tests; code is the authority.
2. Do not add new architecture during cleanup.
3. Establish one canonical semantic execution path.
4. Nothing after user confirmation may silently change the confirmed semantic decision.
5. Useful non-canonical mechanisms may survive as helpers.
6. The AI may delete code directly when behavior is classified and tests prove it is redundant or intentionally obsolete.
7. Record every intentional functionality loss.
8. Architecture documentation is a maintained contract.

## Target responsibility model

| Responsibility | Authority |
|---|---|
| Natural-language interpretation | Small local model |
| Relevant repository facts | Repository knowledge |
| Deterministic structure | Structural layer |
| Semantic ambiguity | User |
| Confirmed semantic decision | Confirmed Plan |
| Repository compatibility check | RepositoryValidation |
| File materialization | Renderer |
| Concrete execution | FileOps |
| Coordination/state | Orchestrator |

## Target flow

```text
User request
    ↓
RepositoryContext
    ↓
Interpretation
    ↓
User decision when ambiguous
    ↓
Confirmed Plan
    ↓
StructuralIR
    ↓
GraphIR
    ↓
RepositoryValidation
    ↓
Renderer
    ↓
FileOps
    ↓
Verify / Commit
```

## Cleanup classification

### KEEP
- StructuralCompletion as lifecycle/structural authority.
- GraphIR as semantic structural representation.
- StructuralIndex as current-worktree knowledge.
- RepositoryIndexer as repository knowledge.
- IntentFileMatcher as candidate/evidence generator.
- RepositorySemanticMemory as historical knowledge.
- ConflictResolutionLayer as repository consistency support.
- RepositoryAwareRenderer as renderer.
- Existing binding mechanisms unless tests prove redundancy.

### MOVE / REDUCE
- StructuralResolver: retain only target/path/instance resolution that cannot change semantic intent.
- Repository memory: evidence/context, never authorization.
- Matcher scores: candidates/evidence, never lifecycle rewrites.
- ApplyEngine: progressively reduce to orchestration/coordination.

### REMOVE FROM CANONICAL DECISION PATH
- IdentityResolver as an authority capable of changing CREATE/MODIFY/EXTEND after confirmation.
- SPLITAnalyzer as an automatic post-confirmation architectural decision.

### REPLANTEA / AUDIT
- AnchorResolver: determine whether it provides unique physical behavior or duplicates renderer composition. Do not delete before classification.

## Removal protocol
For every deletion:
1. Find every consumer.
2. List every observable behavior.
3. Find tests for each behavior.
4. Classify each behavior as preserved elsewhere, intentionally removed, preserved as helper, or unknown.
5. Unknown blocks deletion.
6. Add/adjust tests for preserved behavior.
7. Remove redundant authority/path.
8. Run targeted and relevant broader tests.
9. Update `CLEANUP_FUNCTIONALITY_MATRIX.md`.
10. Update affected architecture docs.
11. Remove obsolete comments/docs describing the old route.

## Required invariants
- Confirmed CREATE remains CREATE.
- Confirmed MODIFY remains MODIFY.
- Confirmed DELETE remains directed at the confirmed target.
- Repository changes after confirmation never cause silent replanning.
- Ambiguity returns to the user.
- Renderer materializes the confirmed decision.
- Helpers can provide evidence without changing the confirmed plan.

## Stop and ask for review when
- two components still have authority over the same semantic decision;
- a deletion would remove behavior whose replacement is unproven;
- tests reveal undocumented behavior;
- the canonical route cannot be established from code;
- preserving a helper requires giving it semantic authority.

## Documentation contract
When architecture changes, update the relevant documents in `docs/architecture/`, especially:
- `SYSTEM_FLOW.md`
- `DECISION_BOUNDARIES.md`
- `REPOSITORY_KNOWLEDGE.md`
- `CLEANUP_FUNCTIONALITY_MATRIX.md`
