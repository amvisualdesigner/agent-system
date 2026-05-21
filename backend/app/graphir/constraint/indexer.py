"""RepositoryIndexer — scans workspace, builds FileNodes.

State Layer: reads workspace, produces FileNode[] + ComponentNode[].
Pure IO — no decision logic, no matching, no rendering.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re

from app.graphir.constraint.models import (
    FileNode,
    ComponentNode,
    ComponentBoundary,
)

logger = logging.getLogger(__name__)


class RepositoryIndexer:
    """Scans a workspace and produces structural indices.

    Phase 1: full implementation with:
    - File walk with .gitignore awareness
    - Export/component extraction via structural heuristics
    - Component boundary detection (line range)
    - Domain inference from path and content
    """

    ALLOWED_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".py", ".css"}
    IGNORED_DIRS = {"node_modules", "dist", "build", ".git", ".opencode", "__pycache__", ".venv", ".pytest_cache"}

    DOMAIN_KEYWORDS: dict[str, list[str]] = {
        "analytics": ["analytics", "kpi", "metric", "dashboard"],
        "sales": ["sales", "revenue", "pipeline", "deal"],
        "marketing": ["campaign", "traffic", "conversion", "marketing"],
        "finance": ["finance", "p&l", "budget", "cost"],
        "admin": ["admin", "settings", "config", "user"],
    }

    def index(
        self, workspace_root: str
    ) -> tuple[dict[str, FileNode], dict[str, ComponentNode]]:
        """Walk workspace, build FileNode for each source file.

        Returns:
            (file_nodes: dict[rel_path, FileNode],
             component_nodes: dict[comp_id, ComponentNode])
        """
        file_nodes: dict[str, FileNode] = {}
        component_nodes: dict[str, ComponentNode] = {}

        for abs_path in self._walk(workspace_root):
            rel_path = os.path.relpath(abs_path, workspace_root)
            try:
                content = self._read_file(abs_path)
            except (IOError, OSError) as e:
                logger.warning("Skipping unreadable file %s: %s", rel_path, e)
                continue

            lines = content.split("\n")
            boundaries = self._extract_boundaries(content, lines)
            exports = [b.name for b in boundaries
                       if b.is_named_export or b.is_default_export]
            imports = self._extract_imports(content)
            component_names = [b.name for b in boundaries]
            domains = self._infer_domains(rel_path, content)
            file_hash = hashlib.sha256(content.encode()).hexdigest()

            file_node = FileNode(
                id=f"file:{rel_path}",
                node_type="file",
                path=rel_path,
                exports=exports,
                imports=imports,
                component_names=component_names,
                domains=domains,
                component_boundaries=boundaries,
                file_hash=file_hash,
                size_bytes=len(content.encode()),
            )
            file_nodes[rel_path] = file_node

            for b in boundaries:
                comp_id = f"comp:{rel_path}:{b.name}"
                component_nodes[comp_id] = ComponentNode(
                    id=comp_id,
                    node_type="component",
                    name=b.name,
                    kind=b.kind,
                    file_id=f"file:{rel_path}",
                    params={},
                    domains=domains,
                )

        return file_nodes, component_nodes

    # ── File walking ───────────────────────────────────────────────

    def _walk(self, root: str) -> list[str]:
        """Recursively walk root, return absolute paths of source files."""
        results: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self.IGNORED_DIRS]
            for fn in filenames:
                ext = os.path.splitext(fn)[1]
                if ext in self.ALLOWED_EXTENSIONS:
                    results.append(os.path.join(dirpath, fn))
        return sorted(results)

    @staticmethod
    def _read_file(path: str) -> str:
        with open(path, "r") as f:
            return f.read()

    # ── Export/component extraction ────────────────────────────────

    EXPORT_PATTERNS: list[tuple[str, bool, bool]] = [
        (r'export\s+default\s+function\s+(\w+)', True, True),
        (r'export\s+default\s+class\s+(\w+)', True, True),
        (r'export\s+default\s+(\w+)', True, False),
        (r'export\s+(const|function|class|interface|type)\s+(\w+)', False, True),
        (r'export\s+const\s+(\w+)\s*[=:]\s*.*React\.FC', False, True),
        (r'export\s+const\s+(\w+)\s*[=:]\s*', False, True),
    ]

    def _extract_boundaries(
        self, content: str, lines: list[str]
    ) -> list[ComponentBoundary]:
        """Detect component/export boundaries using structural heuristics.

        Uses export regex patterns then brace-depth counting to find
        the closing line. This is REPLACEABLE with AST parsing —
        the ComponentBoundary dataclass is the contract.
        """
        boundaries: list[ComponentBoundary] = []

        for i, line in enumerate(lines):
            for pattern, is_default, is_named in self.EXPORT_PATTERNS:
                m = re.search(pattern, line.strip())
                if not m:
                    continue

                name = m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(1)
                end_line = self._find_brace_end(lines, i)
                kind = self._infer_kind(line)

                boundaries.append(ComponentBoundary(
                    name=name,
                    kind=kind,
                    line_start=i + 1,
                    line_end=end_line + 1,
                    is_default_export=is_default or False,
                    is_named_export=is_named or False,
                    export_statement=line.strip(),
                ))
                break  # one pattern per line

        return boundaries

    @staticmethod
    def _find_brace_end(lines: list[str], start: int) -> int:
        """Find where a brace block closes. 0-indexed."""
        depth = 0
        started = False
        for i in range(start, len(lines)):
            for ch in lines[i]:
                if ch == "{":
                    depth += 1
                    started = True
                elif ch == "}":
                    depth -= 1
                    if started and depth <= 0:
                        return i
        return len(lines) - 1

    @staticmethod
    def _infer_kind(line: str) -> str:
        if "interface" in line:
            return "interface"
        if "type " in line or "type\t" in line:
            return "type"
        if "class " in line or "class\t" in line:
            return "class"
        if "function " in line or "function\t" in line:
            return "function"
        if "React.FC" in line:
            return "component"
        if "const " in line:
            return "component"
        return "component"

    # ── Import extraction ──────────────────────────────────────────

    @staticmethod
    def _extract_imports(content: str) -> list[str]:
        return re.findall(r'import\s+.*?\s+from\s+[\'"]([^\'"]+)[\'"]', content)

    # ── Domain inference ───────────────────────────────────────────

    def _infer_domains(self, path: str, content: str) -> list[str]:
        """Infer domain tags from path segments and keyword presence."""
        domains: list[str] = []
        path_lower = path.lower()
        content_lower = content.lower()

        for domain, keywords in self.DOMAIN_KEYWORDS.items():
            if any(kw in path_lower for kw in keywords):
                domains.append(domain)
            elif any(kw in content_lower for kw in keywords):
                domains.append(domain)

        return sorted(set(domains)) if domains else ["generic"]
