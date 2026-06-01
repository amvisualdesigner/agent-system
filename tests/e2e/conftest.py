"""E2E test fixtures: workspaces with git, seed files, cleanup."""

import os
import shutil
import subprocess
import tempfile
import uuid

import pytest

from app.config.settings import settings


def seed_file(workspace: str, rel_path: str, content: str = "") -> str:
    full = os.path.join(workspace, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    if not os.path.isfile(full):
        with open(full, "w") as f:
            f.write(content)
    return rel_path


@pytest.fixture
def e2e_workspace():
    """Git-initialized workspace inside RUNS_DIR for apply_engine tests."""
    tmpdir = tempfile.mkdtemp(prefix="e2e_", dir=settings.RUNS_DIR)
    subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, capture_output=True)

    # Seed required directory structure
    for d in [
        "frontend/src/components/dashboard",
        "frontend/src/components/charts",
        "frontend/src/pages/dashboard",
    ]:
        os.makedirs(os.path.join(tmpdir, d), exist_ok=True)

    yield tmpdir

    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def artifacts_dir():
    """Temp directory inside ARTIFACTS_DIR for apply_engine output."""
    tmpdir = tempfile.mkdtemp(prefix="e2e_artifacts_", dir=settings.ARTIFACTS_DIR)
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def run_context(e2e_workspace, artifacts_dir):
    """RunContext pointing at the seeded workspace."""
    from app.runtime.context import RunContext
    run_id = str(uuid.uuid4())
    return RunContext(
        run_id=run_id,
        base_dir=e2e_workspace,
        workspace=e2e_workspace,
        artifacts=artifacts_dir,
    )
