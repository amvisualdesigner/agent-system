from typing import List
from .loader import SemanticEntry


def compile_context(entries: List[SemanticEntry]) -> str:
    """
    Converts semantic entries into prompt context.
    """

    if not entries:
        return "No semantic context found."

    blocks = ["# Semantic Context\n"]

    for e in entries:
        blocks.append(f"## {e.name}\n")
        blocks.append(e.content.strip())
        blocks.append("\n---\n")

    return "\n".join(blocks)