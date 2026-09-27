# Agent System — Cleanup Functionality Matrix

## Purpose
Every cleanup operation must make functionality loss explicit.

For every removed mechanism answer:
1. What behavior did it provide?
2. Is that behavior intentionally removed?
3. If not, where does it live after cleanup?
4. Which test proves it?

## Initial matrix

| Functionality | Current mechanism | Target after cleanup | Loss allowed? | Evidence |
|---|---|---|---|---|
| Query current worktree | StructuralIndex | Repository knowledge | No | StructuralIndex tests |
| Index components/files | RepositoryIndexer | Repository knowledge | No | Indexer tests |
| Find candidate files | IntentFileMatcher | Context/validation helper | No | Matcher tests |
| Remember identity | RepositorySemanticMemory | Repository knowledge | No | Memory tests |
| Detect stale mappings | ConflictResolutionLayer | RepositoryValidation | No | CRL tests |
| Resolve component boundaries | RepositoryIndexer | Renderer support | No | Renderer/indexer tests |
| Lifecycle CREATE/MODIFY/DELETE | StructuralCompletion | Structural authority | No | Structural tests |
| Graph representation | GraphIR | GraphIR | No | GraphIR tests |
| Automatic CREATE → UPDATE | IdentityResolver | Removed from canonical path | Yes | No-silent-rewrite regression |
| Automatic UPDATE → CREATE | IdentityResolver | Removed from canonical path | Yes | No-silent-rewrite regression |
| Automatic EXTEND decision | IdentityResolver | Removed unless explicitly authorized later | Yes | Regression |
| Automatic alternate-target selection | IdentityResolver | Removed from canonical path | Yes | Ambiguity regression |
| Automatic file split | SPLITAnalyzer | Helper only | Yes for automatic behavior | No-auto-refactor regression |
| Repository-aware rendering | RepositoryAwareRenderer | Renderer | No | Renderer tests |
| Composition materialization | RepositoryAwareRenderer | Renderer | No | Composition tests |
| Anchor-based post-render modification | AnchorResolver | Audit/simplify/possibly remove | Not decided | Call-site + behavior audit |
| Data binding | Existing binding system | Existing binding system | No unless proven redundant | Binding tests |
| File application | FileOps | FileOps | No | Apply tests |
| Datasource bootstrap materialization | Separate applier call (pre-F2) | Single FileOps set (converged at apply time) | No | `test_dry_run_no_write`, suite green |
| PageCreator materialization | Separate pre-write in agent_apply (pre-F3) | Single FileOps set (seeded at apply time) | No | `test_page_creator`, `test_composition_audit` |
| Preview fidelity | Plan-level estimates (pre-F5) | Effective FileOps from deterministic dry_run generation | No | `test_dry_run_no_write::test_preview_fileops_equals_applied_fileops` |
| `dry_run` semantics | Dry run still wrote files (pre-F5) | Preview-only, ZERO writes | No | `test_dry_run_no_write::test_dry_run_generates_ops_but_writes_nothing` |
| Workspace concurrency | None (pre-F4) | Snapshot at confirm; changed worktree at apply → NO WRITE | No | `test_concurrency_no_write` |

## Removal protocol
1. Find consumers.
2. List observable behavior.
3. Map behavior to tests.
4. Classify each behavior: preserved elsewhere, intentionally removed, preserved as helper, or unknown.
5. Unknown blocks deletion.
6. Prove preserved behavior with tests.
7. Remove redundant implementation.
8. Run targeted then broader tests.
9. Update this matrix and affected architecture documents.

## Intentional losses
The following may intentionally disappear from the canonical route:
- silent CREATE → UPDATE;
- silent UPDATE → CREATE;
- silent target substitution;
- automatic architectural refactoring after confirmation.

These are architectural corrections, not regressions.

## Required regressions
Eventually prove:
1. Confirmed CREATE remains CREATE.
2. Confirmed MODIFY remains MODIFY.
3. Confirmed DELETE remains directed at its target.
4. Repository changes after confirmation cause validation conflict, not replanning.
5. Ambiguity reaches the user.
6. Renderer receives the confirmed decision.
7. Matching, memory and split analysis can provide information without changing the confirmed plan.
8. `dry_run` generates the effective FileOps but writes nothing.
9. The operations shown as the confirmation preview equal the operations a real apply writes.
10. A worktree changed between confirm and apply blocks with `NO WRITE` (conflict, reason=concurrency) and reverts the phase to `confirmed`.
