## Purpose

This document defines reusable engineering patterns that AI agents should follow.

The goal is consistency.

---

# Skill: HTTP Requests with Retry

## Requirements

Always:
- use explicit timeouts,
- retry transient failures,
- reuse HTTP clients,
- log retries.

Preferred pattern:

```python
client = httpx.Client(timeout=90, trust_env=False)
```

Retry:
- connect errors,
- timeouts,
- HTTP 5xx.

Do not retry:
- validation failures,
- HTTP 4xx unless explicitly required.

---

# Skill: Structured Logging

## Requirements

Use:

```python
logger = logging.getLogger(__name__)
```

Include:
- attempt number,
- latency,
- status code,
- concise context.

Avoid:
- huge payload dumps,
- secrets in logs,
- noisy debug spam.

---

# Skill: JSON Validation

## Requirements

All external JSON must be validated.

Validation should:
- verify required fields,
- verify types,
- return structured errors,
- preserve raw payload for debugging when useful.

Never assume valid JSON from LLMs.

---

# Skill: Deterministic LLM Calls

Defaults:

```python
temperature = 0.0
```

Prompt rules:
- concise,
- strict,
- explicit expected format,
- avoid unnecessary prose.

Outputs should:
- be validated,
- be retried if malformed,
- fail safely.

---

# Skill: Docker Service Design

Services should:
- include health checks,
- restart automatically,
- expose logs clearly,
- use conservative resource settings initially.

GPU systems:
- prioritize stability first,
- increase utilization gradually.

---

# Skill: GPU Stability

For constrained VRAM environments:
- reduce concurrency first,
- reduce context second,
- reduce utilization third.

Avoid aggressive tuning during initial deployment.

Initial deployment should optimize for:
- no OOMs,
- no crashes,
- predictable latency.

---

# Skill: FastAPI Endpoint Design

Endpoints should:
- validate inputs,
- delegate logic,
- remain thin.

Avoid:
- embedding planner logic,
- large business logic blocks,
- direct infrastructure management.

---

# Skill: Configuration Management

All config should:
- come from environment variables,
- be centralized,
- provide defaults when safe.

Never scatter config constants across the repo.

---

# Skill: Migration Planning

Migration plans should include:
- rationale,
- rollback steps,
- acceptance criteria,
- operational risks,
- observability guidance,
- stability-first defaults.

---

# Skill: Production Safety

Before merging changes:

Verify:
- logs are useful,
- retries are bounded,
- memory usage is acceptable,
- startup works repeatedly,
- shutdown is clean,
- health checks pass.

---

# Skill: AI-Assisted Development

When modifying code:

1. Read surrounding context first.
2. Minimize scope.
3. Preserve conventions.
4. Avoid speculative refactors.
5. Explain risky changes.
6. Prefer explicit behavior.
7. Preserve operational stability.

The repository should evolve incrementally, not chaotically.