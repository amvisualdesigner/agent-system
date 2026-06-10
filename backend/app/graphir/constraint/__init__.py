"""Constraint Graph — repository-aware planning layer.

This module sits between GraphIR and the Execution layer,
adding repository context to file operation decisions.

Architecture:
  GraphIR (semantic, immutable)
      ↓
  ConstraintGraph (repo-aware, deterministic)
      ↓
  Execution Layer (IO only)

Public API:
  ExecutionContext — formal execution isolation
  FileOpDecision   — per-intent file operation decision
  Decision         — CREATE / UPDATE / EXTEND / SPLIT
"""

from app.graphir.constraint.context import ExecutionContext, PipelineState, RenderContext
from app.graphir.constraint.models import (
    Decision,
    FileOpDecision,
    MemoryRecord,
    DeletionRecord,
    ConflictType,
    ResolutionStrategy,
    ConflictRecord,
    ConstraintNode,
    FileNode,
    ComponentNode,
    ComponentBoundary,
    compute_params_hash,
    SplitDirective,
    BlockedDirective,
    RefactoringPlan,
)
from app.graphir.constraint.identity import CanonicalIdentity, build_identities
from app.graphir.constraint.resolver import IdentityResolver
from app.graphir.constraint.memory import RepositorySemanticMemory
from app.graphir.constraint.crl import ConflictResolutionLayer
from app.graphir.constraint.split_analyzer import SPLITAnalyzer
from app.graphir.constraint.diff import (
    StructuralDiffEngine,
    BoundaryValidator,
    BoundaryHealth,
    EditOperation,
    ExtendStrategy,
)
from app.graphir.constraint.generator import ContentGenerator
from app.graphir.constraint.executor import FileOpExecutor, FileOpApplier
__all__ = [
    "ExecutionContext",
    "PipelineState",
    "RenderContext",
    "Decision",
    "FileOpDecision",
    "MemoryRecord",
    "ConflictType",
    "ResolutionStrategy",
    "ConflictRecord",
    "ConstraintNode",
    "FileNode",
    "ComponentNode",
    "ComponentBoundary",
    "compute_params_hash",
    "SplitDirective",
    "BlockedDirective",
    "RefactoringPlan",
    "CanonicalIdentity",
    "build_identities",
    "IdentityResolver",
    "RepositorySemanticMemory",
    "ConflictResolutionLayer",
    "SPLITAnalyzer",
    "StructuralDiffEngine",
    "BoundaryValidator",
    "BoundaryHealth",
    "EditOperation",
    "ExtendStrategy",
    "ContentGenerator",
    "FileOpExecutor",
    "FileOpApplier",
    "detect_deletions",
]
