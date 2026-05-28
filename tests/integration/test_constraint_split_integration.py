"""SPLITAnalyzer + Renderer — integration tests.

Validates the end-to-end flow:
  RepositoryIndexer → IntentFileMatcher → IdentityResolver →
  SPLITAnalyzer → RepositoryAwareRenderer(split_plan=...)

Uses resolved_mapping (Phase 2) to force Level 1 match on a
multi-component file, then verifies SPLIT redirection (Phase 4).

Requires: REPO_ROOT env var.
"""

from app.graphir.backends import BackendConfig
from app.graphir.constraint import (
    ExecutionContext,
    PipelineState,
    RenderContext,
)
from app.graphir.constraint.models import MemoryRecord
from app.graphir.constraint.indexer import RepositoryIndexer
from app.graphir.constraint.matcher import IntentFileMatcher
from app.graphir.constraint.resolver import IdentityResolver
from app.graphir.constraint.renderer import RepositoryAwareRenderer
from app.graphir.constraint.split_analyzer import SPLITAnalyzer

from tests.helpers import FakeWorkspace, make_sample_graph



