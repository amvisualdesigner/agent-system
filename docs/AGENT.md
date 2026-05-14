## Purpose

This repository is developed with AI-assisted engineering workflows.
The AI agent must prioritize:

1. Stability
2. Predictability
3. Observability
4. Reversible changes
5. Architectural consistency
6. Low operational risk

The system is infrastructure-oriented and reliability matters more than cleverness.

---

# Core Engineering Principles

## 1. Prefer Robustness Over Cleverness

Avoid over-engineered abstractions.
Prefer explicit and boring code.

Good:
- Explicit retries
- Explicit validation
- Structured logging
- Clear error handling
- Deterministic outputs

Avoid:
- Metaprogramming
- Hidden side effects
- Dynamic magic
- Implicit behavior
- Complex inheritance

---

## 2. Make Small, Reversible Changes

Changes must:
- be incremental,
- be reviewable,
- be easy to rollback,
- avoid touching unrelated code.

Do not perform broad refactors unless explicitly requested.

---

## 3. Never Break Existing Contracts

Maintain compatibility unless explicitly instructed otherwise.

Preserve:
- API formats
- environment variable names
- file structure
- response schemas
- logging conventions
- retry semantics

If a breaking change is unavoidable:
- document it,
- isolate it,
- explain migration impact.

---

## 4. Reliability First

The project runs long-lived backend services.

All IO operations must:
- use timeouts,
- use retries when appropriate,
- provide structured logging,
- fail gracefully.

This applies to:
- HTTP
- LLM calls
- file operations
- subprocesses
- Docker interactions

---

## 5. Determinism Matters

LLM outputs must be as deterministic as possible.

Defaults:
- temperature=0.0
- strict schemas
- validation required
- retry malformed outputs
- avoid overly long prompts

Never trust raw LLM output without validation.

---

# Coding Standards

## Typing

Use Python typing consistently.

Required:
- explicit return types,
- typed dictionaries where useful,
- Optional / union syntax,
- typed function signatures.

Avoid untyped public functions.

---

## Logging

Use:

```python
logger = logging.getLogger(__name__)
```

Never use:

```python
print()
```

Logs must:
- include useful context,
- support production debugging,
- avoid noisy spam,
- avoid leaking secrets.

Use:
- logger.info
- logger.warning
- logger.error
- logger.exception

---

## Error Handling

Never silently swallow exceptions.

Bad:

```python
except Exception:
    pass
```

Good:

```python
except httpx.TimeoutException as e:
    logger.warning(...)
```

Broad exceptions are only acceptable:
- at process boundaries,
- with logging,
- with safe fallback behavior.

---

## HTTP Clients

HTTP clients must:
- reuse connections,
- use singleton/shared clients when appropriate,
- define explicit timeouts,
- disable environment proxies unless required.

Preferred:

```python
httpx.Client(timeout=90, trust_env=False)
```

Do not create new clients per request.

---

## Configuration

All runtime configuration must come from:
- environment variables,
- settings layer.

Do not hardcode:
- ports,
- hosts,
- model names,
- secrets,
- paths.

Use sensible defaults.

---

## Docker

Containers must:
- restart automatically,
- expose health checks,
- avoid privileged behavior unless required,
- support clean restarts,
- support observability.

GPU systems must prioritize stability over aggressive utilization.

---

# LLM-Specific Rules

## Validation Mandatory

All LLM JSON responses must:
- be validated,
- fail safely,
- provide meaningful errors,
- include retries for malformed outputs.

Never assume valid JSON.

---

## Prompt Design

Prompts should:
- be concise,
- be deterministic,
- define expected output clearly,
- minimize ambiguity.

Avoid:
- extremely large schemas,
- unnecessary prose,
- conflicting instructions.

---

## Tool Calling

Tool execution must:
- validate arguments,
- isolate failures,
- log retries,
- avoid cascading crashes.

---

# Anti-Patterns

## Forbidden Patterns

- print()
- mutable default arguments
- creating HTTP clients per request
- hardcoded secrets
- broad except Exception without logging
- hidden global state
- silent failures
- blocking infinite retries
- sleeping without reason
- parsing unvalidated JSON
- giant refactors during feature work
- mixing infrastructure and business logic
- modifying unrelated files
- adding dependencies without justification
- duplicating utility logic

---

# Review Checklist

Before finalizing changes, verify:

- Is the change minimal?
- Is it reversible?
- Are logs structured?
- Are retries bounded?
- Are timeouts explicit?
- Are configs externalized?
- Is JSON validated?
- Are errors actionable?
- Is VRAM/memory usage reasonable?
- Does it preserve compatibility?

---

# Expected AI Agent Behavior

The AI agent should:
- think conservatively,
- optimize for operational stability,
- avoid unnecessary abstractions,
- preserve architecture,
- explain risky changes,
- prefer boring and maintainable solutions.

The AI agent must act like a senior infrastructure engineer, not a prototype hacker.