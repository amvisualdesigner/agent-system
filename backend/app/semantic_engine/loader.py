import os
from dataclasses import dataclass
from typing import List, Dict

@dataclass
class SemanticEntry:
    path: str
    name: str
    content: str
    type: str


def load_semantic_entries(base_path: str) -> List[SemanticEntry]:
    """
    Loads all markdown files from semantic/ directory.
    """
    entries: List[SemanticEntry] = []

    if not os.path.exists(base_path):
        raise ValueError(f"Semantic path does not exist: {base_path}")
    
    if "/skills/" in base_path:
        entry_type = "skill"
    elif "/components/" in base_path:
        entry_type = "component"
    elif "/layouts/" in base_path:
        entry_type = "layout"
    else:
        entry_type = "unknown"

    for root, _, files in os.walk(base_path):
        for file in files:
            if not file.endswith(".md"):
                continue

            full_path = os.path.join(root, file)

            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()

            name = file.replace(".md", "")

            entries.append(
                SemanticEntry(
                    path=full_path,
                    name=name,
                    content=content,
                    type=entry_type
                )
            )

    return entries