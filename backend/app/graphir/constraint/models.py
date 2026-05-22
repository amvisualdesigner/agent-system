"""Constraint Graph models — shared types for all constraint modules.

This module defines the data types used across the Constraint Graph layer:
- Decision types (CREATE, UPDATE, EXTEND, SPLIT)
- FileOpDecision: per-intent file operation decision
- Conflict types and records for the CRL
- ConstraintNode base types (used in Phases 1+)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Decision types ─────────────────────────────────────────────────

class Decision(str, Enum):
    """Output of the matching phase — what to do with an intent."""
    CREATE  = "create"
    UPDATE  = "modify"
    EXTEND  = "extend"
    SPLIT   = "split"
    DELETE  = "delete"


@dataclass(frozen=True)
class FileOpDecision:
    """The result of matching a single intent to a file.

    Produced by IntentFileMatcher. Consumed by RepositoryAwareRenderer.
    MUST_NOT_MODIFY_SEMANTICS is always True — ConstraintGraph only
    decides physical mapping, never semantic content.
    """
    intent_id: str
    graphir_node_id: str
    decision: Decision
    target_file: str
    confidence: float = 0.0
    rationale: str = ""
    must_not_modify_semantics: bool = True


# ── MemoryRecord (used in Phase 6a memory upgrade) ────────────────

@dataclass(frozen=True)
class MemoryRecord:
    """Persistent identity→file binding with component name.

    Replaces bare dict[str, str] storage.
    component_name is NEVER empty — enforced at construction and load time.
    """
    fingerprint: str
    file_path: str
    component_name: str


# ── DeletionRecord (used in Phase 6a DELETE detection) ────────────

@dataclass(frozen=True)
class DeletionRecord:
    """State-diff result: a component to remove.

    Produced by detect_deletions() from the difference between
    persisted memory and the current intent graph.
    """
    fingerprint: str
    file_path: str
    component_name: str


# ── Conflict types (used in Phase 3 CRL) ──────────────────────────

class ConflictType(str, Enum):
    STALE_MAPPING        = "stale_mapping"
    STRUCTURAL_MISMATCH  = "structural_mismatch"
    DUPLICATE_BINDING    = "duplicate_binding"
    MISSING_TARGET       = "missing_target"
    CONTENT_DRIFT        = "content_drift"


class ResolutionStrategy(str, Enum):
    REBIND     = "rebind"
    KEEP       = "keep"
    INVALIDATE = "invalidate"
    SPLIT      = "split"


@dataclass
class ConflictRecord:
    """A single detected inconsistency between memory and repo state.

    Produced by ConflictResolutionLayer.resolve().
    """
    identity_fingerprint: str
    conflict_type: ConflictType
    expected_file: str
    actual_file: str | None = None
    severity: float = 0.5
    resolution: ResolutionStrategy = ResolutionStrategy.KEEP
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Constraint Node base types (used in Phases 1+) ────────────────

@dataclass(frozen=True)
class ConstraintNode:
    """Base type for all constraint graph nodes."""
    id: str
    node_type: str  # "intent" | "file" | "component"


@dataclass(frozen=True)
class FileNode(ConstraintNode):
    """Represents an existing file in the repository.

    Produced by RepositoryIndexer at index time.
    Contains structural signals extracted from file content.
    """
    path: str
    exports: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    component_names: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    component_boundaries: list[ComponentBoundary] = field(default_factory=list)
    file_hash: str = ""
    size_bytes: int = 0
    canonical_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ComponentNode(ConstraintNode):
    """Represents a logical component/symbol within a file."""
    name: str
    kind: str = "component"  # "component" | "function" | "class" | "interface" | "type"
    file_id: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    domains: list[str] = field(default_factory=list)
    doc_string: str = ""


# ── ComponentBoundary (precomputed at index time) ─────────────────

@dataclass(frozen=True)
class ComponentBoundary:
    """Precise location of a component/export within a file.

    Precomputed by RepositoryIndexer at index time.
    Used by RepositoryAwareRenderer for line-range merge.
    NEVER computed at render time.
    """
    name: str
    kind: str  # "component" | "function" | "class" | "interface" | "type"
    line_start: int   # 1-indexed, inclusive
    line_end: int     # 1-indexed, inclusive
    is_default_export: bool = False
    is_named_export: bool = False
    export_statement: str = ""


# ── CanonicalIdentity fingerprinting (used in Phase 1 matcher) ───

def compute_params_hash(params: dict[str, Any]) -> str:
    """Deterministic hash of sorted params keys."""
    sorted_json = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(sorted_json.encode()).hexdigest()[:12]


# ── RefactoringPlan (used in Phase 4 structural analysis) ─────────

@dataclass
class SplitDirective:
    source_file: str
    new_file: str
    components_to_extract: list[str] = field(default_factory=list)
    imports_to_migrate: list[str] = field(default_factory=list)
    imports_to_rewire: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class BlockedDirective:
    intent_id: str
    file_path: str
    reason: str


@dataclass
class RefactoringPlan:
    splits: list[SplitDirective] = field(default_factory=list)
    blocks: list[BlockedDirective] = field(default_factory=list)

    def is_blocked(self, file_path: str) -> bool:
        return any(b.file_path == file_path for b in self.blocks)

    def is_splitting(self, file_path: str) -> bool:
        return any(s.source_file == file_path for s in self.splits)

    def new_file_for(self, file_path: str, component: str) -> str | None:
        for s in self.splits:
            if s.source_file == file_path and component in s.components_to_extract:
                return s.new_file
        return None
