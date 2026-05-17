import os
from dataclasses import dataclass
from typing import List

@dataclass
class SemanticEntry:
    path: str
    name: str
    content: str
    type: str


_DIR_TYPE_MAP = {
    "architecture": "architecture",
    "components": "component",
    "data_contracts": "contract",
    "layouts": "layout",
    "patterns": "pattern",
    "skills": "skill",
}


def _detect_type(root: str, base_path: str) -> str:
    rel = os.path.relpath(root, base_path)
    parts = rel.split(os.sep)
    if parts:
        dir_name = parts[0]
        return _DIR_TYPE_MAP.get(dir_name, "unknown")
    return "unknown"


def load_semantic_entries(base_path: str) -> List[SemanticEntry]:
    """
    Loads all markdown files from semantic/ directory.
    """
    entries: List[SemanticEntry] = []

    if not os.path.exists(base_path):
        raise ValueError(f"Semantic path does not exist: {base_path}")

    for root, _, files in os.walk(base_path):
        for file in files:
            if not file.endswith(".md"):
                continue

            full_path = os.path.join(root, file)

            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()

            name = file.replace(".md", "")
            entry_type = _detect_type(root, base_path)

            entries.append(
                SemanticEntry(
                    path=full_path,
                    name=name,
                    content=content,
                    type=entry_type,
                )
            )

    return entries