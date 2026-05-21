# Test structure

```
tests/
├── unit/                  # 100% deterministic, no IO, no env vars
│   ├── test_graphir.py    # Core GraphIR models (nodes, edges, layout, validator)
│   ├── test_graphir_phase1.py  # Builder, pipeline, ReactBackend
│   └── test_constraint_boundary.py  # GraphIR Purity Boundary (NEW)
│
├── integration/           # Needs IO, API calls, or env vars
│   ├── test_graphir_phase3.py  # Embedding API calls
│   ├── test_graphir_phase6.py  # Full end-to-end pipeline
│   └── test_constraint_smoke.py  # Constraint Graph wiring (NEW)
│
├── legacy_contract/       # Tests depending on the contract registry
│   └── test_graphir_phase2.py  # Intent decomposition, coverage, param extraction
│
├── examples/              # Mixed tests not yet migrated
│   └── test_graphir_phase4.py  # Governance tracking (partially needs env vars)
│
└── README.md
```

## Rules

### unit/ (Pure Core)
- Zero IO, zero env vars, zero API calls
- 100% deterministic: same input → same output always
- Run with: `python3 -m pytest tests/unit/ -q`
- New Constraint Graph pure logic goes here (identity, matcher, structural analysis)

### integration/ (State + Execution)
- May need `REPO_ROOT` env var or other env vars
- May access filesystem (via test fixtures, not production paths)
- Tests validate that components wire together correctly
- New Constraint Graph state/execution tests go here (indexer, memory, renderer, CRL)

### legacy_contract/
- Tests that depend on the SkillContract registry
- May need `REPO_ROOT` and other env vars
- Tests the pre-Intent decomposition path (IntentNode-based)

### examples/
- Transitional directory for mixed tests
- Migrate to unit/ or integration/ as they are cleaned up
