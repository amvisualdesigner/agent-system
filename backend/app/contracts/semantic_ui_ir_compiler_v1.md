# Semantic UI IR Compiler — Design Contract v1

## 0. Identity

System Name:
Semantic UI IR Compiler

---

## 1. Scope (Compiler Layer Only)

This system ONLY includes:

- AST ingestion
- Component tree construction
- Import resolution (enrichment)
- Slot resolution (binding)
- Emission (FileOps generation)
- Post-validation (SymbolGraph + FileOps validation)

---

### NOT included:
- Planning / skill selection (`skill_ir`)
- Prompt interpretation
- Execution runtime
- UI rendering in browser

---

## 2. Execution Philosophy

Execution Model:

Flexible input → Strict deterministic compilation

Rules:
- Input may be partial or incomplete
- Output MUST be fully deterministic or fail
- Never assume missing data
- Never use heuristics or implicit inference
- Ambiguity = RuntimeError

---

## 3. Compiler Pipeline (STRICT ORDER)

1. build_component_tree(ast, config, ctx)
2. resolve_imports(root)
3. resolve_slots(root)
4. emit_tree(root)
5. SymbolGraph(root) [read-only validation]
6. validate_fileops(fileops)

RULE:
Order is immutable. No step may be skipped or reordered.

---

## 4. Data Ownership Rules

### AST
Source of truth for structure only:
- nodes
- slot declarations

MUST NOT contain runtime state.

---

### Derived State

ComponentNode derived fields:
- resolved_imports → produced by resolve_imports
- slot_bindings → produced by resolve_slots

RULE:
Derived fields MUST NOT be manually modified after creation.

---

## 5. Slot System (Phase 5 Core)

### 5.1 Binding Rule

For each node:
- Slots processed in declaration order
- Children assigned greedily
- Matching strictly by: child.component ∈ slot.allowed_types
- No heuristics
- No fallback
- No guessing

---

### 5.2 Failure Rules

System MUST fail if:
- child cannot be assigned to any slot
- required slot is empty
- resolve_slots not executed before emission

Error type:
RuntimeError("Phase5SlotViolation")

---

### 5.3 Composition Rule

If node.slots exists:
- node.children MUST NOT be used in emission
- only node.slot_bindings is valid

---

## 6. Import Resolution (Phase 4 Core)

Rule:
resolved_imports MUST be fully computed before emission.

If missing:
RuntimeError("MissingImportResolution")

No fallback allowed.

---

## 7. Emission Rules

### 7.1 emit_file purity

emit_file(node) MUST:
- NOT mutate state
- NOT resolve imports
- NOT resolve slots
- ONLY consume precomputed IR state

---

### 7.2 Template Role

Templates are:
- Output formatting layer only
- NOT structural logic
- NOT compositional logic

---

## 8. Determinism Rules

System guarantees:
- No randomness
- No embedding-based decisions
- No heuristics
- No implicit sorting
- No undefined iteration order

If ambiguity exists → FAIL FAST

---

## 9. Failure Philosophy

FAIL FAST > DEGRADED OUTPUT

Reason:
- prevents silent corruption
- avoids heuristic drift
- enforces compiler correctness

---

## 10. Examples

### Valid Pipeline

AST → resolve_imports → resolve_slots → emit_tree → FileOps

---

### Invalid Behavior (FORBIDDEN)

- guessing slot assignment
- emitting before slot resolution
- using children directly when slots exist
- heuristic ordering of components
- fallback rendering when data is missing

---

## 11. System Mental Model

This system is NOT a renderer.

It is:

Deterministic UI IR Compiler

---

## 12. Core Guarantee

If this contract is followed:

- Output is deterministic
- Output is reproducible
- Output is structurally correct
- No hidden heuristics exist