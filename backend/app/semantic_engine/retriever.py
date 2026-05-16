from typing import List
from .loader import SemanticEntry


def is_semantic_task(task: str) -> bool:
    keywords = [
        "dashboard", "kpi", "chart", "analytics",
        "metric", "report", "visualization"
    ]
    return any(k in task.lower() for k in keywords)


def retrieve(task: str, entries: List[SemanticEntry], top_k: int = 5, min_score: int = 3) -> List[SemanticEntry]:
    """
    Simple keyword-based retrieval (NO embeddings yet).
    """

    task_lower = task.lower()
    scored = []

    for entry in entries:
        content_lower = entry.content.lower()
        name_lower = entry.name.lower()

        score = 0

        # name match (strong signal)
        if name_lower in task_lower:
            score += 5

        # keyword overlap (weak signal)
        for word in task_lower.split():
            if word in content_lower:
                score += 1

        # APPLY THRESHOLD HERE (single source of truth)
        if score >= min_score:
            scored.append((score, entry))

    if not scored:
        return []

    scored.sort(key=lambda x: x[0], reverse=True)

    return [entry for _, entry in scored[:top_k]]