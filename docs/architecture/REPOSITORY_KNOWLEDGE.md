# Agent System — Repository Knowledge

## Purpose
Reuse existing repository knowledge rather than creating a second repository-intelligence system.

## Knowledge vs decision

Knowledge includes:
- component/file existence;
- paths and imports;
- component boundaries;
- historical identity mappings;
- likely matching files;
- current repository changes.

Decisions include:
- create another component;
- modify an existing one;
- select/delete a particular instance;
- split a file;
- replace one target with another.

Knowledge may inform a decision. It must not silently become a different decision after confirmation.

## Existing mechanisms

### StructuralIndex
Current-worktree query interface for component instances, paths, existence and deterministic create paths.

Keep as deterministic repository knowledge.

### RepositoryIndexer
Indexes repository files/components and captures imports, exports and component boundaries.

Strong candidate source for future `RepositoryContext`.

### IntentFileMatcher
Ranks likely matching files/components.

Keep as candidate/evidence generation. Scores must not rewrite lifecycle.

### RepositorySemanticMemory
Persists useful historical identity information.

Keep as knowledge/history. It must not decide a new action after confirmation.

### ConflictResolutionLayer
Compares remembered state with current repository state and invalidates stale mappings.

Strong candidate for future `RepositoryValidation`.

### Component boundaries
Support precise renderer modifications. Preserve if tests demonstrate their usefulness.

### Binding system
Already represents framework-agnostic data relationships and lowers them to renderer-specific expressions. Do not replace it without evidence.

## Future RepositoryContext
Provide a compact, relevant repository view before interpretation. Prefer structured facts and relevant files/components over raw repository dumps. Deterministic filtering is important because the local model is small.

## Future RepositoryValidation
Check whether a confirmed plan is still executable:
- target existence;
- target identity;
- required files;
- expected relationships;
- relevant repository changes;
- stale mappings;
- conflicts.

It must not invent a replacement plan.

## IdentityResolver
Current behavior can turn repository evidence into CREATE/UPDATE/EXTEND decisions. That is the wrong authority after confirmation.

Preserve useful identity/matching knowledge, but remove autonomous lifecycle rewriting from the canonical path.

## SPLITAnalyzer
May remain as an explicit refactoring/helper capability. It can detect overloaded files and propose splits. It must not automatically execute a split because the renderer encountered an overloaded file.

## Design rule
Prefer:
```text
Repository Knowledge → Evidence → Explicit decision
```
over:
```text
Repository Knowledge → Hidden decision
```
