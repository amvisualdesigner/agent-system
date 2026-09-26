# Agent System — Decision Boundaries

## Core principle
There must be one authority for each semantic decision. The user is the final authority when materially different interpretations remain possible.

## Boundary

```text
Repository
    ↓
Repository Knowledge
    ↓
Small Model Interpretation
    ↓
User Decision / Confirmation
    ↓
Confirmed Plan
    ↓
Deterministic Structure
    ↓
Repository Validation
    ↓
Renderer
    ↓
FileOps
```

## Small-model constraint
The local model is intentionally small because it runs on constrained local hardware. Do not compensate by requiring larger reasoning tasks from the model.

Instead:
- precompute deterministic repository facts;
- provide compact, relevant context;
- use structured representations;
- use deterministic code for identity, existence, paths and consistency;
- ask the user when a semantic choice is ambiguous.

The model interprets; it is not the global authority.

## Interpretation
The model may understand language, identify candidate capabilities, extract parameters, identify likely targets, detect ambiguity and propose alternatives.

It must not silently invent missing intent.

Example: if the user says “Añade un filtro al dashboard” and an existing filter makes both “create another” and “modify existing” plausible, ask the user.

## RepositoryContext
RepositoryContext improves interpretation before confirmation. It may include relevant pages, component instances, capabilities, paths, imports, boundaries, composition, bindings, constraints and validated historical identity.

Context is evidence, not authorization.

## Confirmation
Confirmation freezes the semantic decision.

After confirmation, execute that decision or ask the user if it is no longer executable.

## StructuralIR / GraphIR
These represent the confirmed decision. They are not a second intent interpreter.

## RepositoryValidation
After confirmation, validation answers only:
> Can the confirmed plan still be executed against the current repository?

Valid outcomes:
- `VALID` — the confirmed plan executes as confirmed.
- `CONFLICT` — the confirmed plan is no longer (or cannot be) executed as confirmed. Reported to the user; nothing is written.

There is no third outcome: any state that is not VALID is surfaced as a CONFLICT for the user. Validation never invents a replacement plan.

If a conflict requires a semantic alternative, return to the user.

Forbidden:
```text
confirmed plan → repository changed → system chooses alternative → execute
```

Required:
```text
confirmed plan → repository changed → ask user
```

CREATE against an existing target (and no explicit new-instance hint) is a CONFLICT — the repository is never overwritten silently.

## PageCreator (physical operation)
User-chosen `create_new` generates page CREATE ops. Physical validation at generation and at apply:
- target absent → CREATE FileOp;
- target exists → CONFLICT (no overwrite, no alternate path, no memory, no heuristics);
- deterministic per snapshot.

## Renderer
Materialize an already-decided structure. Technical rendering choices are allowed; semantic intent changes are not.

## FileOps
Execute concrete operations. No intent reinterpretation.

## Orchestrator
Coordinate state and stages. No domain decisions.

## Forbidden patterns
- CREATE silently becoming UPDATE after confirmation.
- Target A silently becoming target B.
- Approved modification silently turning into a file split/refactor.

## Helper rule
Helpers may calculate candidates, scores, identities, conflicts or refactoring opportunities. They must not turn those facts into a different confirmed decision without passing through the appropriate authority.
