# Agent System Architecture

## 1. System Overview

This system transforms natural-language intents into deterministic UI graph mutations over a target repository (BI dashboards). It does **not** generate code from scratch — it reconciles user intent against existing file structures, computes a diff plan, and executes file operations via an immutable intermediate representation pipeline.

### High-Level Pipeline

```
User Input (NL)
    │
    ▼ ── IntentInterpreter (LLM) ──── capability_catalog.json
InterpretationDraft
    │
    ▼ ── Human confirms via PlanCompiler (deterministic)
CompiledPlan
    │
    ▼ ── ApplyEngine ── StructuralIndex.from_worktree()
StructuralIR  (diff plan: CREATE/MODIFY/DELETE/KEEP)
    │
    ▼ ── GraphIRBuilder + GraphIRPipeline
GraphIR  (immutable DAG)
    │
    ▼ ── BindingResolver (data_access.json)
ResolvedBindings
    │
    ▼ ── UIIRCompiler
UIComponentTree
    │
    ▼ ── ReactBackend.render_tree()
FileOp[]  (create/modify/delete files)
    │
    ▼ ── FileOpApplier (sole mutation authority)
Disk writes
```

## 2. Core Concepts

### Intent
A structured representation of what the user wants. Originates as natural language, passes through `InterpretationDraft` (LLM output), then `ConfirmedIntent` (human-verified), then `IntentAction` (verb + target + params).

### Actions / Plans
An action is a `(verb, target_capability, params)` triple. Verbs are `create`, `modify`, `remove`, `keep`. A `CompiledPlan` bundles `skill_ir`, `semantic_frame`, `intents`, and `actions`. The plan is compiled deterministically — no LLM involvement after `ConfirmedIntent`.

### Capabilities
Named semantic component types (e.g. `presentation.kpi_row`, `presentation.timeseries`, `layout.page`). Defined in `capability_catalog.json` and registered in `STRUCTURAL_SCHEMA` within `structural_completion.py`. Each has required/optional params and a `CompletionMode`.

### Graph Nodes
`GraphIRNode` — an immutable node in the dashboard DAG. Fields: `id`, `type` (e.g. "KpiRow"), `data` (business params), `metadata` (intent provenance). Identity is `type:instance_id`. Created exclusively by `GraphIRBuilder.build_from_structural()`.

### UI Components
`UIComponentNode` — framework-agnostic tree node produced by `UIIRCompiler`. Fields: `id`, `component` (type name), `props` (resolved from BindingResolver), `children`, `layout_hints`, `instance_only`, `data_imports`. This is the **only** contract consumed by backend renderers.

### Intermediate Representations (IRs)
| IR | Purpose | Mutable? | Created By |
|----|---------|----------|------------|
| InterpretationDraft | LLM proposal, not yet confirmed | Mutable | IntentInterpreter |
| ConfirmedIntent | Human-verified intent | Mutable | /agent/confirm |
| CompiledPlan | Deterministic plan from confirmed intent | Mutable | PlanCompiler |
| StructuralIR | Diff plan: CREATE/MODIFY/DELETE/KEEP per capability | Frozen | complete_structure() |
| GraphIR | Immutable DAG of semantic components | Frozen | GraphIRBuilder |
| GraphIRLayout | Spatial constraints derived from GraphIR | Frozen | LayoutDerivationEngine |
| ResolvedBindings | Contract params → component props | Mutable | BindingResolver |
| UIComponentTree | Framework-agnostic component tree | Mutable | UIIRCompiler |

### StructuralIR.operations (Diff Plan)
Each operation is a dict:
```python
{"action": "CREATE"|"MODIFY"|"DELETE"|"KEEP"|"INSTANCE",
 "target": "capability_name",
 "payload": {...},        # params for CREATE/MODIFY/INSTANCE
 "instance_only": bool,   # True = KEEP + no file regeneration
 "instance_id": str|None} # specific instance for DELETE
```

The `INSTANCE` action means "capability exists in repo, do not regenerate, but instantiate in container". Used for `create`-on-existing capabilities.

### Execution Bifurcation: Constraint Graph vs Direct Renderer

Within `apply_engine()`, two render paths exist, gated by the `FEATURE_FLAGS["constraint_graph"]` flag:

**Constraint Graph path** (when `constraint_graph=True`):
1. `RepositoryIndexer` indexes the workspace into `file_nodes` + `component_nodes`
2. `RepositorySemanticMemory` loads persisted identity→file mappings
3. `IntentFileMatcher` matches GraphIR nodes to files
4. `IdentityResolver` resolves decisions (CREATE/UPDATE/EXTEND/SPLIT)
5. `SPLITAnalyzer` detects overloaded files needing split
6. `detect_deletions()` finds components absent from current intent but present in memory
7. `RepositoryAwareRenderer` produces FileOps with content generation + structural merge
8. `memory.merge()` + `memory.save()` persist new identity mappings

**Direct Renderer path** (when `constraint_graph=False`, the current default for most operations):
1. `ReactBackend.render()` → pure UIComponentTree → FileOp pipeline
2. No file indexing, no memory, no identity matching
3. DELETE operations injected separately from `StructuralIR.operations`

Both paths converge at `FileOpApplier.apply()` for disk writes and post-apply verification.

## 3. Full Pipeline Breakdown

### Stage 1: User Input → Intent Parsing

**Input:** Raw natural language string + optional conversation history.

**Process:**
1. Contract selection via keyword overlap (`_CONTRACT_KEYWORDS` in `interpreter.py`)
2. Action verb detection via `_ACTION_TRIGGERS`
3. LLM call via `llm_chat()` with catalog slice as system prompt
4. Post-LLM validation: action verbs against catalog, capability existence checks
5. Instance hint resolution for multi-instance capabilities (e.g. "line" → LineChart vs Timeseries)

**Output:** `InterpretationDraft` with status `ok` | `needs_clarification` | `unsupported`, proposed actions, worktree capabilities.

**Key files:** `backend/app/intent/interpreter.py`, `llm_client.py`, `catalog/loader.py`

### Stage 2: Human Confirms → Plan Compilation

**Input:** `ConfirmedIntent` (actions + params + contract_id from UI).

**Process:**
1. Load contract from `skill_registry`
2. Build `SkillIR` from confirmed params + contract defaults
3. Build `semantic_frame` from confirmed actions
4. Build `intents` list from actions
5. Build `actions` list (zero-loss invariant: each IntentAction → one plan action)
6. Validate no action is lost (semantic_action_count > 0 ⇒ plan_actions not empty)

**Output:** `CompiledPlan` — the single deterministic plan consumed by ApplyEngine.

**Key files:** `backend/app/intent/plan_compiler.py`, `contracts/skill_registry.py`

### Stage 3: Structural Resolution

**Input:** `CompiledPlan` + workspace `StructuralIndex`.

**Process** (all in `complete_structure()`):
1. **Contract inference:** extract capabilities from contract `ast_template`
2. **Action matching:** match semantic actions to capability names via `_match_actions_to_capabilities()`
   - OBJECT_KEYWORDS, suffix map, template keys, substring matching
3. **Post-pass A:** metrics signal → auto-MODIFY kpi_row
4. **Post-pass B:** "modify dashboard" → MODIFY all presentational children
5. **Lifecycle resolution:** `_resolve_action()` — deterministic rules:
   - exists + delete → DELETE
   - exists + modify → MODIFY
   - exists + no verb → KEEP
   - not exists + create → CREATE
   - not exists + no verb → CREATE (contract default)
6. **CREATE-on-existing detection:** user verb is CREATE but capability exists → KEEP + `instance_only=True`
7. **Contract injection guard:** if user has actions + capability has no action_verb + not in repo → SAFE_SKIP (no spurious CREATE)
8. **Anchor preservation** (`_ensure_graph_viability`): if 0 builder nodes would result, preserve the most structural capability as MODIFY (prefers existing on-disk over scaffold)
9. **Composition sync** (`_sync_composition_parents`): child CREATE/DELETE/instance_only → parent page MODIFY
10. **Composition child expansion** (`_expand_composition_children`): parent MODIFY/CREATE → KEEP children → INSTANCE

**Output:** `StructuralIR` (frozen tuple of `ResolvedCapability` + operations list).

**Key files:** `backend/app/engine/structural_completion.py`, `structural_index.py`

### Stage 4: Graph IR Build

**Input:** `StructuralIR` + optional `StructuralResolution`.

**Process:**
1. Filter operations to CREATE, MODIFY, and INSTANCE only (DELETE/KEEP are not built into the graph)
2. Each operation → one `Intent` → one `GraphIRNode`
3. Elect root: `layout.page` wins, fallback to first node
4. Create edges from root to each child node using `EDGE_ROLE_MAP`
5. Freeze: validate DAG, purity, all nodes reachable

**Output:** `GraphIR` — immutable, validated DAG.

**Key files:** `backend/app/graphir/builder.py`, `models.py`, `pipeline.py`, `validator.py`

### Stage 5: Binding Resolution (pre-compilation)

**Input:** Contract params from `ConfirmedIntent`.

**Process** (5-step pipeline in `BindingResolver.resolve()`):
1. Resolve Page slices (composition data flow via JSVariable refs)
2. Resolve per-component v4 bindings:
   - Step 1: resolve `from` field in contract_params
   - Step 2: validate presence (required=True + missing → error)
   - Step 3: validate arity (scalar vs array — warning only)
   - Step 4: apply transform (registered function in transform registry)
   - Step 5: emit to component_props with provenance
3. Dual-write diff (F1 diagnostic — compares slice vs binding resolution)
4. Emit unconsumed param warnings

**Output:** `ResolvedBindings` — component_props, provenance, page_data_source, imports.

**Key files:** `backend/app/binding/resolver.py`, `models.py`, `config/data_access.json`

### Stage 6: UI IR Compile

**Input:** `GraphIR` + `GraphIRLayout` + `ResolvedBindings` + component signatures.

**Process** (in `UIIRCompiler.compile()`):
1. Recursively build `UIComponentNode` tree from GraphIR edges
2. Each node gets props from `ResolvedBindings.component_props` (NOT from node.data — hard rule)
3. Required prop enforcement: every prop in signature `required_props` must be in resolved props
4. Phase 6 validation: children under Page must have no data_imports
5. Compute semantic fidelity score (consumed/total contract params)

**Output:** `UIComponentTree` — root UIComponentNode + warnings + fidelity + provenance.

**Key files:** `backend/app/graphir/compiler.py`, `ui_ir.py`

### Stage 7a: Datasource Bootstrap (pre-render)

**Input:** Workspace + DatasourceContract.

**Process:**
1. Load `DatasourceContract` from `.opencode/data_contract.json` in workspace
2. If hook file (`useDashboardData.ts`) doesn't exist, generate datasource infrastructure files (types + hooks)
3. Validate all `data_access.json` slices reference real contract fields

**Key files:** `backend/app/datasource/contract.py`, `backend/app/graphir/backends/react_backend.py:generate_datasource_artifacts()`

### Stage 7: Render

**Input:** `UIComponentTree` + `BackendConfig`.

**Process:**
1. Flatten tree BFS
2. For each node: skip if instance_only, error if binding_missing + required, skip if no generator
3. Generate content via registered `ComponentGenerator` functions
4. Inject composition children via `_render_children()`
5. Inject Page hook declarations for data sources
6. Inject data imports
7. Normalize exports to match filename
8. Resolve file path via `FilePathResolver` (respects overrides)
9. Emit `FileOp` with pipeline_route = "renderer"

**Output:** `list[FileOp]` — create/modify file operations.

**Key files:** `backend/app/graphir/backends/react_backend.py`, `path_resolver.py`

### Stage 7b: Post-render — DELETE injection + replace_pairs + verify

**Input:** `FileOp[]` from renderer.

**Process:**
1. **DELETE injection:** For each `StructuralIR.operation` with `action=DELETE`, resolve target files via `_discover_repo_capability_files()` and append delete FileOps. Multi-instance resolution uses `instance_hint` for disambiguation.
2. **Replace pairs:** For each `(old_cap, new_cap)` in `StructuralIR.replace_pairs`, inject delete FileOps for old capability files.
3. **FileOp validation:** `validate_fileops()` checks invariants.
4. **Disk write:** `FileOpApplier.apply()` atomically writes/deletes files.
5. **Verify:** `verify_worktree()` runs `tsc --noEmit` and/or `npm run build` on the workspace.
6. **Git flow:** `git add -A`, `git commit` (unless verify failed, in which case diff is captured but commit is skipped).

**Output:** Execution result with status, diff, operations, fidelity report, and verify result.

**Key files:** `backend/app/engine/apply_engine.py`, `executor/diff_generator.py`, `engine/verify_worktree.py`

## 4. Data Models

### IntentAction
- **File:** `backend/app/intent/models.py`
- **Purpose:** Single verb + target in a confirmed intent
- **Fields:** `verb: str`, `target_capability: str`, `params: dict`, `confidence: float`, `instance_hint: str|None`
- **Created by:** `ConfirmedIntent` construction in `/agent/confirm`
- **Consumed by:** `PlanCompiler.compile_plan()`

### InterpretationDraft
- **File:** `backend/app/intent/models.py`
- **Purpose:** LLM proposal before human confirmation
- **Fields:** `interpretation_id`, `status` (ok|needs_clarification|unsupported), `contract_id`, `proposed_actions`, `alternatives`, `params_proposed`, `worktree_capabilities`, `clarification_question`
- **Created by:** `IntentInterpreter.interpret()`
- **Consumed by:** `/agent/confirm` (via state persistence)

### ConfirmedIntent
- **File:** `backend/app/intent/models.py`
- **Purpose:** Human-verified intent — sole input to PlanCompiler
- **Fields:** `contract_id`, `contract_version`, `actions: list[IntentAction]`, `params`, `user_message`, `interpretation_id`
- **Created by:** `/agent/confirm` endpoint
- **Consumed by:** `PlanCompiler.compile_plan()`

### CompiledPlan
- **File:** `backend/app/intent/models.py`
- **Purpose:** Deterministic plan consumed by ApplyEngine
- **Fields:** `skill_ir: dict`, `semantic_frame: dict`, `intents: list[dict]`, `contract_id`, `actions: list[dict]`
- **Created by:** `PlanCompiler.compile_plan()`
- **Consumed by:** `ApplyEngine.apply_engine()`

### RunPhase (state machine)
- **File:** `backend/app/intent/models.py`
- **Purpose:** Governs lifecycle of interpret → confirm → apply
- **States:** `INTERPRETING` → `AWAITING_CONFIRMATION` → `CONFIRMED` → `APPLYING` → `COMPLETED` | `FAILED` | `CANCELLED`
- **Transitions:** Self-transitions always allowed. Terminal states: COMPLETED, FAILED, CANCELLED

### ResolvedCapability
- **File:** `backend/app/engine/structural_completion.py`
- **Purpose:** Frozen capability with definitive param ownership and lifecycle action
- **Fields:** `name`, `params`, `mode` (CompletionMode), `action` (CREATE|MODIFY|DELETE|KEEP), `provenance`, `instance_only`, `instance_id`, `instance_hint`
- **Created by:** `complete_structure()` loop
- **Consumed by:** `StructuralIR.operations` property → GraphIRBuilder

### StructuralIR
- **File:** `backend/app/engine/structural_completion.py`
- **Purpose:** Frozen diff plan — operations on the repo (NOT desired final state)
- **Fields:** `contract_id`, `contract_version`, `capabilities: tuple[ResolvedCapability]`, `param_provenance`, `confidence`, `completion_warnings`, `layout_hints`, `replace_pairs`
- **Created by:** `complete_structure()`
- **Consumed by:** `GraphIRBuilder.build_from_structural()`, `GraphIRPipeline.run_from_structural()`

### GraphIRNode
- **File:** `backend/app/graphir/models.py`
- **Purpose:** Single semantic component in the dashboard DAG
- **Fields:** `id` (type:instance_id), `type` (e.g. "KpiRow"), `component_instance_path`, `data` (frozen dict), `metadata` (frozen dict)
- **Created by:** `GraphIRBuilder._add_intent_node()`
- **Consumed by:** `UIIRCompiler.compile()`, backend renderers

### GraphIR
- **File:** `backend/app/graphir/models.py`
- **Purpose:** Immutable validated DAG. Only produced by `GraphIRDraft.freeze()`.
- **Fields:** `nodes: dict[id, GraphIRNode]`, `edges: list[GraphIREdge]`, `layout: GraphIRLayout`, `params`
- **Created by:** `GraphIRBuilder.build_from_structural()`
- **Consumed by:** `GraphIRPipeline`, `UIIRCompiler`, backend renderers

### UIComponentNode
- **File:** `backend/app/graphir/ui_ir.py`
- **Purpose:** Framework-agnostic UI tree node — the only rendering contract
- **Fields:** `id`, `component`, `props` (from BindingResolver), `data_imports`, `instance_only`, `binding_missing_props`, `children`, `layout_hints`
- **Created by:** `UIIRCompiler._build_node()`
- **Consumed by:** `ReactBackend.render_tree()`

### ResolvedBindings
- **File:** `backend/app/binding/models.py`
- **Purpose:** Contract between BindingResolver and Compiler — all resolved props
- **Fields:** `component_props: dict[type, dict[prop, value]]`, `provenance`, `consumed_params`, `unconsumed_params`, `page_data_source`, `imports`
- **Created by:** `BindingResolver.resolve()`
- **Consumed by:** `UIIRCompiler.compile()`

### FileOp
- **File:** `backend/app/graphir/models.py`
- **Purpose:** Atomic file operation — output contract of every renderer
- **Fields:** `action: str` (create|modify|delete), `path: str`, `content: str`, `pipeline_route: PipelineRoute` (constraint|renderer|composition_sync|delete_inject|unknown), `metadata`
- **Created by:** `ReactBackend.render_tree()`, `RepositoryAwareRenderer`, `FileOpExecutor`
- **Consumed by:** `FileOpApplier.apply()` (sole mutation authority)

## 5. Mutation System

### How Changes Happen

The system never mutates files directly. Every mutation passes through the FileOp contract:

```
StructuralIR.operations
    → GraphIR (builder filters to CREATE/MODIFY/INSTANCE only)
    → UIComponentTree (compiler resolves props)
    → FileOp[] (renderer generates file content)
    → FileOpApplier (atomic writes to disk)
```

### DELETE Operations
- Originate from `StructuralIR.operations` with `action=DELETE`
- The GraphIR builder **skips** DELETE operations (no node built for deleted capability)
- Deleted capabilities have no GraphIR node, no UI component, no file content
- File deletion is handled by the execution layer (FileOp with `action=delete`)
- DELETE is also detected by `detect_deletions()` in the constraint layer via state-diff: components present in persisted memory but absent from current intent

### MODIFY Operations
- Originate from `StructuralIR.operations` with `action=MODIFY`
- The GraphIR builder includes the node with updated params (stable capability identity)
- The UI compiler resolves props from the updated binding data
- The renderer generates new file content for the component
- The FileOpApplier writes the new content atomically

### CREATE Operations
- Originate from `StructuralIR.operations` with `action=CREATE`
- Same flow as MODIFY but the file does not yet exist
- CREATE-on-existing (user says "create" but file exists) → `instance_only=True` → KEEP + no file regeneration

### Composition / Parent-Child Relationships
- Parent-child relationships are defined by `GraphIREdge` objects in the graph
- The graph is always a DAG with exactly one root (enforced by `GraphIRValidator`)
- Edges have `EdgeRole`: CONTAINS, PRIMARY, SUPPORTING (semantic only — no layout encoding)
- **Composition sync** (3E): when a child capability is CREATE/DELETE/instance_only, the parent page is promoted to MODIFY so it regenerates with correct imports/JSX references
- **Composition child expansion** (C2): when a parent Page is MODIFY/CREATE, all KEEP slot children are promoted to INSTANCE so they appear in the graph (otherwise the builder would skip them)
- The renderer's `_render_children()` generates the JSX composition for each parent

### Instance-only
- A capability with `instance_only=True` has `action=KEEP` but carries "create" intent
- The node participates in composition (parent imports/uses it) but its source file is NOT regenerated
- The renderer skips file generation when `uinode.instance_only` is True
- This prevents CREATE-on-existing from destroying the existing implementation

### Replace Pairs
- Preserved in `StructuralIR.replace_pairs` as `[(old_cap, new_cap), ...]`
- Generated from REPLACE action verbs in semantic frame
- At file-op time, the old capability's files are deleted via injected `FileOp(action="delete", pipeline_route="delete_inject")`
- Consistency validated post-hoc by `validate_replace_consistency()`

### Instance Hints for Multi-Instance DELETE
- Defined in `_INSTANCE_HINTS` mapping in `interpreter.py` (e.g., "line" → LineChart vs "timeseries" → Timeseries)
- Applied during intent interpretation and carried through `instance_hint` field
- At DELETE resolution, matched against real filenames via `_stem_matches_hint()` (exact stem match, not substring)

### Route Transparency (3F)
Each FileOp carries a `pipeline_route` label:
- `"constraint"` — produced by constraint-aware renderer
- `"renderer"` — produced by standard backend renderer
- `"composition_sync"` — produced by composition sync post-pass
- `"delete_inject"` — delete operation injected by the system
- `"unknown"` — default for legacy/unlabeled operations

## 6. File/Module Responsibilities

### `backend/app/intent/`
| File | Responsibility | Inputs | Outputs |
|------|---------------|--------|---------|
| `models.py` | Data models: IntentAction, InterpretationDraft, ConfirmedIntent, CompiledPlan, RunPhase, RunState | — | Shared data contracts |
| `interpreter.py` | LLM-based NL → InterpretationDraft. Only entrypoint LLM. Catalog-aware. | User message, index_snapshot | InterpretationDraft |
| `plan_compiler.py` | Deterministic ConfirmedIntent → CompiledPlan. No LLM, no StructuralIndex. | ConfirmedIntent + contract | CompiledPlan |
| `llm_client.py` | HTTP client for vLLM-compatible API. 3 retries, JSON cleaning. | Prompt, system_prompt | Parsed JSON dict |

### `backend/app/engine/`
| File | Responsibility | Inputs | Outputs |
|------|---------------|--------|---------|
| `structural_completion.py` | Complete_structure: SR+CR → StructuralIR. Action matching, lifecycle resolution, composition sync, anchor preservation. | SemanticResolution, ContractResolution, contract, StructuralIndex | StructuralIR |
| `structural_index.py` | Query interface over real worktree. `from_worktree()`, `exists()`, `resolve_path()`, `get_instances()` | Workspace path | StructuralIndex dataclass |
| `apply_engine.py` | Orchestrator: StructuralIndex.from_worktree() → complete_structure() → GraphIRPipeline → BindingResolver → ReactBackend → FileOpApplier. Git flow, artifact writing, verification. | run_id, plan, context | Execution result dict |
| `state_adapter.py` | Legacy state serialization. Multi-instance support via dict[str, list[ComponentInstanceInfo]]. | Workspace structural state | Adapted state dict |
| `aliases.py` | FILENAME_ALIASES centralization. | — | Name maps |
| `gate.py` | Policy gate validation. | Plan, index | Gate result (blocked/allowed) |
| `errors.py` | AmbiguousStructuralTargetError, other pipeline errors. | — | Exception classes |
| `verify_worktree.py` | Post-apply verification: `tsc --noEmit` / `npm run build`. | Workspace | Verify result dict |

### `backend/app/graphir/`
| File | Responsibility | Inputs | Outputs |
|------|---------------|--------|---------|
| `models.py` | GraphIRNode, GraphIREdge, GraphIRLayout, GraphIR, GraphIRDraft, FileOp. Core IR definitions. | — | Shared data contracts |
| `builder.py` | GraphIRBuilder: StructuralIR → GraphIR via GraphIRDraft. 1:1 capability→node. | StructuralIR + optional StructuralResolution | GraphIR |
| `compiler.py` | UIIRCompiler: GraphIR → UIComponentTree. Binding-aware, required prop enforcement. | GraphIR, ResolvedBindings, signatures | UIComponentTree |
| `pipeline.py` | GraphIRPipeline: run_from_structural() orchestrates builder + layout + enrichment. | StructuralIR | (GraphIR, GraphIRLayout) |
| `ui_ir.py` | UIComponentNode, UIComponentTree, UIGeneratorContext. Framework-agnostic tree. | — | Shared data contracts |
| `semantic_frame.py` | OBJECT_KEYWORDS for 3B action binding. StructuredSemanticFrame dataclass. | — | Keyword maps, dataclasses |
| `intent.py` | Intent dataclass, resolve_graphir_type_from_capability(), resolve_edge_role_from_capability(). | — | Intent + resolution helpers |
| `layout.py` | LayoutDerivationEngine: GraphIR → GraphIRLayout. | GraphIR + preferences | GraphIRLayout |
| `validator.py` | GraphIRValidator: DAG check, single root, all reachable. | GraphIR | Validation result |
| `boundary.py` | enforce_graphir_purity(): no filesystem keys in GraphIR. | Node data/metadata | Raises on violation |
| `path_resolver.py` | FilePathResolver: resolve node type + config → relative file path. Respects overrides. | GraphIRNode + BackendConfig | Relative file path |
| `debug.py` | Debug/audit utilities for GraphIR. | GraphIR | Debug info |

### `backend/app/graphir/backends/`
| File | Responsibility | Inputs | Outputs |
|------|---------------|--------|---------|
| `base.py` | BackendRenderer ABC, BackendConfig dataclass. | — | Abstract contract |
| `react_backend.py` | ReactBackend: UIComponentTree → FileOp[]. ComponentGenerator dispatch, signature override, children injection, hook hoisting. | UIComponentTree + BackendConfig | FileOp[] |

### `backend/app/graphir/constraint/`
| File | Responsibility |
|------|---------------|
| `context.py` | ExecutionContext, PipelineState, RenderContext — formal execution isolation |
| `executor.py` | FileOpExecutor (pure EditOperation→FileOp computation) and FileOpApplier (sole mutation authority, atomic writes) |
| `deletion.py` | detect_deletions() — state-diff based component removal (pure, no IO) |
| `renderer.py` | RepositoryAwareRenderer — produces FileOps from decisions, content generation, structural merge |
| `diff.py` | StructuralDiffEngine, BoundaryValidator — edit operations |
| `generator.py` | ContentGenerator — pure content generation |
| `models.py` | Decision, FileOpDecision, MemoryRecord, DeletionRecord, etc. |
| `identity.py` | CanonicalIdentity, build_identities |
| `resolver.py` | IdentityResolver |
| `memory.py` | RepositorySemanticMemory |
| `crl.py` | ConflictResolutionLayer |
| `indexer.py` | RepositoryIndexer |
| `matcher.py` | Identity matcher |
| `split_analyzer.py` | SPLITAnalyzer |

### `backend/app/binding/`
| File | Responsibility | Inputs | Outputs |
|------|---------------|--------|---------|
| `models.py` | ResolvedBindings, BindingProvenance, BindingDiff, BindingDiffItem | — | Shared data contracts |
| `resolver.py` | BindingResolver.resolve() — 5-step pipeline: from field → presence → arity → transform → emit | contract_params | ResolvedBindings |

### `backend/app/signature/`
| File | Responsibility |
|------|---------------|
| `extractor.py` | Extract component signatures (props interfaces, types, imports) from .tsx files |
| `prop_mapper.py` | PropMapper, DataSourceIR, MISSING_REQUIRED_PROPS. Legacy param aliases (deprecated in favor of BindingResolver). |

### `backend/app/executor/`
| File | Responsibility | Status |
|------|---------------|--------|
| `worktree_manager.py` | Git worktree creation/isolation. ensure_worktree() + create_worktree(). | Active |
| `diff_generator.py` | Generate git diff from staged changes. | Active |
| `patch_executor.py` | Legacy file operation executor (create/modify/delete). | DEPRECATED |
| `patch_executor_dumb.py` | Even more legacy executor. | DEPRECATED |
| `skill_resolver.py` | Legacy skill resolution. | DEPRECATED |

### `backend/app/api/`
| File | Endpoint | Responsibility |
|------|----------|---------------|
| `agent_interpret.py` | `POST /agent/interpret` | Call IntentInterpreter, persist InterpretationDraft, transition phase |
| `agent_confirm.py` | `POST /agent/confirm` | Validate phase, build ConfirmedIntent, call PlanCompiler, persist plan |
| `agent_apply.py` | `POST /agent/apply` | Validate phase+gate, call apply_engine, transition to completed/failed |

### `orchestrator/`
| File | Responsibility |
|------|---------------|
| `main.py` | FastAPI app: POST /run, /run/{id}/confirm, /run/{id}/apply, GET /stream/{id}, GET /runs |
| `graph.py` | LangGraph StateGraph: interpret → confirm → validate_plan → call_apply → return_result. Conditional entry point. |
| `state.py` | AgentState TypedDict: task, plan, execution, trace, phase, interpretation, confirmed_intent, plan_preview |
| `nodes/interpret.py` | LangGraph node: calls /agent/interpret, handles clarification/error |
| `nodes/confirm.py` | LangGraph node: calls /agent/confirm, emits plan_preview_ready SSE |
| `nodes/validate_plan.py` | Validates plan has actions or valid skill_ir |
| `nodes/call_apply.py` | LangGraph node: calls /agent/apply |
| `nodes/return_result.py` | Persists snapshot, emits result SSE |
| `backend_client.py` | HTTP client for backend API calls (interpret, confirm, apply) |
| `sse.py` | SSE event emitter for streaming responses |
| `store.py` | Snapshot persistence (JSON files by run_id) |

## 7. Execution Rules

### What triggers file deletion
- A `StructuralIR.operation` with `action=DELETE`. The operation originates from `_resolve_action()` when the user verb is "remove"/"delete"/etc. AND the capability exists in the repo.
- In the constraint graph layer, `detect_deletions()` via state-diff: components in persisted memory but absent from the current intent graph.
- File deletion is executed by `FileOpApplier._apply_one()` with `fop.action="delete"`.

### What triggers graph modification
- Any CREATE or MODIFY operation in `StructuralIR.operations` causes a new or updated `GraphIRNode` in the graph.
- `instance_only=True` capabilities also produce graph nodes (for composition) but skip file generation.
- Composition sync promotes parent pages to MODIFY when children change.
- Composition child expansion promotes KEEP slot children to INSTANCE when parent Page is modified.

### How UI updates are derived
- UI updates = `FileOp[]` produced by `ReactBackend.render_tree()`.
- Each `UIComponentNode` generated by `UIIRCompiler` drives one `FileOp`.
- The renderer uses registered `ComponentGenerator` functions keyed by `uinode.component`.
- Props come exclusively from `ResolvedBindings` — the BindingResolver is the sole translator from contract params to component props.
- Component signatures from the worktree override generic rendering for known component types.

### What is deterministic vs inferred
**Deterministic (pure, no LLM):**
- PlanCompiler (`plan_compiler.py`) — 100% deterministic from ConfirmedIntent + contract
- complete_structure() — lifecycle decisions, action matching, composition sync
- GraphIRBuilder — StructuralIR → GraphIR
- BindingResolver — contract params → ResolvedBindings (registry-driven)
- UIIRCompiler — GraphIR → UIComponentTree
- ReactBackend — UIComponentTree → FileOp[]
- FileOpApplier — FileOp[] → disk writes
- LayoutDerivationEngine — GraphIR → GraphIRLayout

**Inferred (LLM, non-deterministic):**
- IntentInterpreter — the ONLY LLM entrypoint. Maps NL to capabilities using catalog.
- Contract selection (keyword overlap is deterministic, but the LLM chooses action verbs and params)

### Ordered pipeline (non-negotiable order)
1. IntentInterpretation (LLM)
2. PlanCompilation (deterministic)
3. StructuralIndex.from_worktree()
4. complete_structure() → StructuralIR
5. GraphIRPipeline.run_from_structural() → (GraphIR, GraphIRLayout)
6. BindingResolver.resolve() → ResolvedBindings
7. UIIRCompiler.compile() → UIComponentTree
8. BackendRenderer.render() → FileOp[]
9. FileOpApplier.apply() → disk mutations
10. (Optional) verify_worktree.py → tsc/build validation

## 8. Glossary

| Term | Definition (as used in this codebase) |
|------|----------------------------------------|
| **Capability** | A named semantic component type with required/optional params. Registered in STRUCTURAL_SCHEMA and capability_catalog.json. E.g. `presentation.kpi_row`, `layout.page`. |
| **Contract** | A named blueprint that maps a domain (e.g. dashboard.sales_overview) to a set of capabilities, renderer files, and param schemas. Defined in skill_registry.py. |
| **StructuralIR** | Frozen diff plan. NOT a desired state — a set of operations (CREATE/MODIFY/DELETE/KEEP) on the repo. |
| **GraphIR** | Immutable semantic component DAG. Single source of truth for what exists in the dashboard. |
| **UIComponentTree** | Framework-agnostic renderer contract. Extracted from GraphIR via UIIRCompiler. |
| **ResolvedBindings** | Pre-compiled mapping from contract params to component props. Single source of prop values. |
| **BindingIR** | The v4 binding system in data_access.json. Per-component, per-prop resolution rules with transforms. |
| **FileOp** | Atomic file operation: create/modify/delete a file with given content. The output contract of every renderer. |
| **instance_only** | A capability lifecycle state: KEEP action + instance_only=True. The capability exists on disk and should not be regenerated, but participates in parent composition. |
| **Composition sync** | Post-pass that promotes parent pages to MODIFY when child capabilities are CREATE/DELETE/instance_only. |
| **Anchor preservation** | If all capabilities would result in 0 builder nodes (no CREATE/MODIFY), the most structural capability is promoted to MODIFY/SAFE_COMPLETE to keep the graph viable. |
| **PipelineRoute** | Label on each FileOp indicating which pipeline stage produced it: constraint, renderer, composition_sync, delete_inject, or unknown. |
| **MISSING_REQUIRED_PROPS** | Hard compilation error: a required prop (from component signature) has no binding resolution. Stops the pipeline. |
| **SSOT gate** | Required Props enforcement: every prop in a component's required_props list must be present in ResolvedBindings. |
| **Dual-write** | F0 diagnostic: both Page slices and v4 bindings are resolved independently, then compared for divergence. Does not affect output. |
| **Transform registry** | Registered transform functions (identity, items, wrap, value, label) referenced by name from data_access.json. Applied in step 4 of the binding pipeline. |
