"""Offline script: extract structural patterns from .tsx examples → catalog JSONs.

Usage:
    python scripts/build_examples_catalog.py

Output:
    backend/app/examples/catalog/{domain}.json

This is a BUILD-TIME tool. The runtime NEVER reads .tsx files directly.
"""

import json
import re
from pathlib import Path

EXAMPLES_DIR = Path("backend/app/examples/react")
CATALOG_DIR = Path("backend/app/examples/catalog")

RENDERER_SCHEMA_VERSION = "2026-05"
CATALOG_VERSION = 1

IMPORT_RE = re.compile(r'^import\s.*?;$', re.MULTILINE)
EXPORT_COMPONENT_RE = re.compile(
    r'export\s+(?:default\s+)?(?:const|function)\s+(\w+)'
)
COMPONENT_REF_RE = re.compile(
    r'<([A-Z]\w+)(?:\s|>|/)'
)


def extract_imports(text: str) -> list[str]:
    return IMPORT_RE.findall(text)


def extract_components(text: str) -> list[str]:
    return EXPORT_COMPONENT_RE.findall(text)


def extract_layouts(text: str, components: list[str]) -> list[str]:
    return [c for c in components if "Layout" in c]


def extract_composition(text: str, domain_components: set[str]) -> list[list[str]]:
    """Find which components reference other domain components in JSX.

    Uses domain-wide component set for cross-file composition tracking.
    """
    refs = COMPONENT_REF_RE.findall(text)
    file_comps = set(extract_components(text))
    edges = []
    for comp in file_comps:
        for ref in refs:
            if ref != comp and ref in domain_components:
                edges.append([comp, ref])
    return edges


def build_catalog(domain: str, tsx_files: list[Path]) -> dict:
    # Pass 1: collect all components across the domain
    all_imports: list[str] = []
    all_components: set[str] = set()
    file_texts: list[tuple[str, str]] = []

    for fpath in tsx_files:
        text = fpath.read_text()
        comps = extract_components(text)
        all_imports.extend(extract_imports(text))
        all_components.update(comps)
        file_texts.append((fpath.name, text))

    # Pass 2: cross-file composition
    all_composition: list[list[str]] = []
    for fname, text in file_texts:
        edges = extract_composition(text, all_components)
        all_composition.extend(edges)

    return {
        "version": CATALOG_VERSION,
        "renderer_schema_version": RENDERER_SCHEMA_VERSION,
        "domain": domain,
        "imports": sorted(set(all_imports)),
        "components": sorted(all_components),
        "layouts": sorted(
            l for l in all_components if "Layout" in l
        ),
        "composition": [list(e) for e in sorted(set(
            tuple(e) for e in all_composition
        ))],
    }


def main():
    CATALOG_DIR.mkdir(parents=True, exist_ok=True)

    domains: dict[str, list[Path]] = {}
    for tsx in sorted(EXAMPLES_DIR.rglob("*.tsx")):
        domain = tsx.parent.name
        domains.setdefault(domain, []).append(tsx)

    for domain, files in sorted(domains.items()):
        catalog = build_catalog(domain, files)
        out_path = CATALOG_DIR / f"{domain}.json"
        out_path.write_text(json.dumps(catalog, indent=2) + "\n")
        print(f"  ✓ {out_path}  ({len(files)} files, {len(catalog['components'])} components)")


if __name__ == "__main__":
    print("Building examples catalog...")
    main()
    print("Done.")
