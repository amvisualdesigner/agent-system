# CanonicalIdentity Specification

## 1. Identity Schema

```
CanonicalIdentity {
    component_name: str          # Required — from GraphIRNode.type
    capability_id: str           # Required — from intent_capability metadata
    domain: tuple[str, ...]      # Derived — from capability top-level, or metadata
    params_hash: str             # Derived — deterministic hash of node.data
    graphir_node_id: str         # Traceability only — NOT part of fingerprint
}
```

### Field derivation rules

| Field | Source | Overridable |
|-------|--------|-------------|
| `component_name` | `node.type` | No |
| `capability_id` | `node.metadata["intent_capability"]` | No |
| `domain` | `capability_id.split(".")[0]` if metadata has no `domain` key | Via `node.metadata["domain"]` |
| `params_hash` | `sha256(json.dumps(node.data, sort_keys=True))[:12]` | No |
| `graphir_node_id` | `node.id` | No |

### Fingerprint composition

```
fingerprint = f"{capability_id}:{sorted(domain)}:{params_hash}"
```

Example: `presentation.kpi_row:presentation:44136fa355b3`

The fingerprint is the **sole equality anchor**. Two identities are SAME if and only if their fingerprints match byte-for-byte.

---

## 2. Identity Invariance Rules

The following changes do **NOT** produce a new identity:

| Change | Rationale |
|--------|-----------|
| File path / filename | Location is not identity; file can move |
| Export name formatting | Naming convention is presentation, not semantics |
| Content whitespace / comments | Node.data is stable, codegen is downstream |
| Execution context (run_id, workspace_root) | Identity is environment-independent |
| Number of files in workspace | Workspace state does not change intent semantics |
| GraphIR node ID (`graphir_node_id`) | Node ID is an execution artifact, not intent-semantic |
| Resolved mapping | Mapping is a memory artifact, not an identity property |

---

## 3. Identity Mutation Rules

The following changes **DO** produce a new identity:

| Change | Mechanism | Example |
|--------|-----------|---------|
| Capability ID changes | `fingerprint` includes `capability_id` | `presentation.kpi_row` → `presentation.timeseries` |
| Domain changes | `fingerprint` includes `domain` | `presentation` → `analytics` |
| Structural param changes | `params_hash` changes | Adding a new prop to node.data |

### What counts as "structural param change"

`params_hash` is `sha256(json.dumps(node.data, sort_keys=True))[:12]`.

Currently, any change to `node.data` produces a new hash and therefore a new identity. This is intentionally conservative for Phase 1:

- Adding a field → new identity (props shape changed)
- Removing a field → new identity
- Changing a value → new identity

**Future refinement (Phase 4+):** Only hash fields that affect rendering output.
Non-functional fields (display labels, CSS class names) should be excluded.

---

## 4. Collision Semantics

Three relationship levels between identities:

### SAME (fingerprint match)

`fp(a) == fp(b)`

- MUST map to the same file
- Enforced by identity-first matching in IdentityResolver
- No threshold involved; this is a hard invariant
- The resolved_mapping (Phase 2+) is keyed by fingerprint, so any identity
  with that fingerprint automatically targets the same file

### RELATED (fingerprint mismatch, score overlap)

`fp(a) != fp(b)` AND `score(a, file) >= EXTEND_THRESHOLD`

- Scoring fallback applies
- Produces ranked candidates in IntentFileMatcher
- IdentityResolver applies thresholds to decide UPDATE / EXTEND / CREATE
- This is the only zone where threshold tuning matters
- RELATED pairs may share tokens (e.g., `presentation.kpi_row` and
  `presentation.kpi` share `kpi`)

### DISTINCT (no meaningful overlap)

`fp(a) != fp(b)` AND `score(a, file) < EXTEND_THRESHOLD` for all files

- Always produces CREATE for a new file
- No candidate overlap with existing files
- Example: `presentation.kpi_row` vs a file exporting `UserSettings`

---

## 5. Determinism Rule

> Same input → same decisions. Always. No exceptions.

```
∀ graph, file_nodes, resolved_mapping:
    run(matcher, resolver, graph, file_nodes, resolved_mapping)
    = run(matcher, resolver, graph, file_nodes, resolved_mapping)
```

### Enforced properties

| Property | Enforced by |
|----------|-------------|
| No randomness | IntentFileMatcher is deterministic; no sampling |
| No external IO | matcher and resolver are Pure Core |
| No hidden state | resolved_mapping is the only mutable input |
| No dynamic thresholds | UPDATE_THRESHOLD and EXTEND_THRESHOLD are class constants |
| No hashing collisions | sha256 truncated to 12 hex chars (48 bits) — collision risk < 1e-12 |

### What determinism means for Phase 2+

When RepositorySemanticMemory writes `.opencode/semantic_memory.json`, the
resolved_mapping is loaded from that file **before** resolver runs. This means:

- Two runs with the same memory file → same decisions
- Two runs with different memory files → different decisions (expected — memory evolved)
- Two runs with no memory file → same decisions as Phase 1 (backward compatible)

Determinism is per-input-snapshot, not globally.

---

## Appendix: Architecture Boundary

```
ProcessIntent (Execution Layer)
  │
  ├─ 1. build_identities(graph)                # Pure Core
  ├─ 2. IntentFileMatcher.match(graph, files)   # Pure Core → ranked candidates
  ├─ 3. IdentityResolver.resolve(candidates)    # Pure Core → FileOpDecision[]
  ├─ 4. RepositoryAwareRenderer.render(decisions) # State Layer (content gen)
  └─ 5. apply_engine writes files               # Execution Layer (IO)

Identity is established in step 1-3.
Step 4-5 consume identity, they do not modify it.
```
