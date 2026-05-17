import re
from dataclasses import dataclass, field


TASK_MODES = {"semantic_skill", "generic"}

MIN_SEMANTIC_SCORE = 2
MIN_COMPOSITION_SCORE = 1

SEMANTIC_PATTERNS = {
    "dashboard", "analytics", "kpi", "timeseries",
    "widget", "layout", "component", "panel",
    "chart", "visualization", "report", "grid",
    "metrics",
}

COMPOSITION_VERBS = {"create", "build", "design", "generate", "compose"}

NON_COMPOSITION_PATTERNS = {
    "fix", "debug", "refactor", "rename",
    "cleanup", "optimize", "test",
}


@dataclass
class TaskClassification:
    mode: str
    semantic_score: int
    composition_score: int
    matched_patterns: list[str] = field(default_factory=list)


def normalize_task(task: str) -> str:
    task = task.lower()
    task = re.sub(r"[^a-z0-9\s]", " ", task)
    task = re.sub(r"\s+", " ", task).strip()
    return task


def classify_task(task: str) -> TaskClassification:
    task_lower = normalize_task(task)

    negative_score = sum(
        1 for v in NON_COMPOSITION_PATTERNS
        if re.search(rf"\b{re.escape(v)}\b", task_lower)
    )

    matched_patterns = [
        k for k in SEMANTIC_PATTERNS
        if re.search(rf"\b{re.escape(k)}\b", task_lower)
    ]
    semantic_score = max(0, len(matched_patterns) - negative_score)
    composition_score = sum(
        1 for v in COMPOSITION_VERBS
        if re.search(rf"\b{re.escape(v)}\b", task_lower)
    )

    if semantic_score >= MIN_SEMANTIC_SCORE and composition_score >= MIN_COMPOSITION_SCORE:
        return TaskClassification(
            mode="semantic_skill",
            semantic_score=semantic_score,
            composition_score=composition_score,
            matched_patterns=matched_patterns,
        )

    return TaskClassification(
        mode="generic",
        semantic_score=semantic_score,
        composition_score=composition_score,
        matched_patterns=matched_patterns,
    )
