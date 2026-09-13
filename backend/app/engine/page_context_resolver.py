from __future__ import annotations

import re
from dataclasses import dataclass, field


_CONTEXT_RE = re.compile(
    r'(?:in|on|to)\s+(\w+)\s+(?:dashboard|page|overview|screen|area|section)'
    r'|(\w+)\s*(?:dashboard|page|overview|screen)',
    re.I,
)


@dataclass
class PageChoice:
    id: str
    page: str
    description: str

    def to_dict(self) -> dict:
        return {"id": self.id, "page": self.page, "description": self.description}


@dataclass
class ContextDecision:
    requested_context: str | None = None
    matched_page: str | None = None
    confidence: float = 0.0
    pages_found: list[str] = field(default_factory=list)
    choices: list[PageChoice] = field(default_factory=list)

    @property
    def needs_clarification(self) -> bool:
        return self.requested_context is not None and self.confidence == 0.0

    def to_dict(self) -> dict:
        return {
            "requested_context": self.requested_context,
            "matched_page": self.matched_page,
            "confidence": self.confidence,
            "pages_found": list(self.pages_found),
            "choices": [c.to_dict() for c in self.choices],
        }


def extract_requested_context(user_message: str) -> str | None:
    match = _CONTEXT_RE.search(user_message)
    if not match:
        return None
    return (match.group(1) or match.group(2)).lower()


def _find_pages_from_index(index) -> list[str]:
    prefix_map = index.detect_component_prefixes()
    return sorted([
        name for name in prefix_map
        if "Page" in name
    ])


def resolve(
    user_message: str,
    existing_pages: list[str] | None = None,
    structural_index=None,
) -> ContextDecision:
    if existing_pages is None and structural_index is not None:
        existing_pages = _find_pages_from_index(structural_index)
    existing_pages = existing_pages or []

    context = extract_requested_context(user_message)

    if context is None:
        return ContextDecision(pages_found=existing_pages)

    matched = [p for p in existing_pages if context.lower() in p.lower()]

    if matched:
        return ContextDecision(
            requested_context=context,
            matched_page=matched[0],
            confidence=1.0,
            pages_found=existing_pages,
        )

    choices = []
    for p in existing_pages:
        choices.append(PageChoice(
            id=f"page:{p}",
            page=p,
            description=f"Añadir a página existente {p}",
        ))
    suggested = f"{context.title()}DashboardPage"
    choices.append(PageChoice(
        id="create_new",
        page=suggested,
        description=f"Crear nueva página {suggested}",
    ))

    return ContextDecision(
        requested_context=context,
        matched_page=None,
        confidence=0.0,
        pages_found=existing_pages,
        choices=choices,
    )
