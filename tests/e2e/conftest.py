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


PAGE_TSX_CONTENT = """\
import React from 'react';
import { Timeseries } from './components/Timeseries';
import { KpiRow } from './components/KpiRow';
import { useDashboardData } from '../../hooks/useDashboardData';

interface PageProps {}

export const Page: React.FC<PageProps> = () => {
  const { kpiData, chartData } = useDashboardData();
  return (
    <div className="page">
      <KpiRow data={kpiData} />
      <Timeseries data={chartData.timeseries} />
    </div>
  );
};
"""

KPI_ROW_TSX_CONTENT = """\
import React from 'react';
import { Card } from '../../ui/Card';

interface KpiRowProps {
  data: Array<{ label: string; value: number }>;
}

export const KpiRow: React.FC<KpiRowProps> = ({ data }) => {
  return (
    <div className="kpi-row">
      {data.map((item) => (
        <Card key={item.label}>
          <span>{item.label}</span>
          <span>{item.value}</span>
        </Card>
      ))}
    </div>
  );
};
"""

TIMESERIES_TSX_CONTENT = """\
import React from 'react';
import { Card } from '../../ui/Card';

interface TimeseriesProps {
  data: Array<{ x: string; y: number }>;
  title?: string;
}

export const Timeseries: React.FC<TimeseriesProps> = ({ data, title }) => {
  return (
    <Card>
      <h3>{title || 'Trend'}</h3>
      <div className="timeseries-chart">
        {data.map((point) => (
          <span key={point.x}>{point.y}</span>
        ))}
      </div>
    </Card>
  );
};
"""

LINECHART_TSX_CONTENT = """\
import React from 'react';
import { Card } from '../../ui/Card';

interface LineChartProps {
  data: Array<{ label: string; value: number }>;
}

export const LineChart: React.FC<LineChartProps> = ({ data }) => {
  return (
    <Card>
      <svg viewBox="0 0 400 200">
        {data.map((d, i) => (
          <circle key={i} cx={i * 50} cy={200 - d.value} r={3} />
        ))}
      </svg>
    </Card>
  );
};
"""


def _init_git_workspace(prefix: str) -> str:
    """Create a temp dir with git init and return the path."""
    tmpdir = tempfile.mkdtemp(prefix=prefix, dir=settings.RUNS_DIR)
    subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, capture_output=True)
    os.makedirs(os.path.join(tmpdir, "src", "pages", "dashboard", "components"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "src", "hooks"), exist_ok=True)
    return tmpdir


def _seed_and_commit(workspace: str, msg: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=workspace, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=workspace, capture_output=True)


@pytest.fixture
def composition_workspace():
    """Full workspace: Page + KpiRow + Timeseries + LineChart."""
    tmpdir = _init_git_workspace("comp_")
    seed_file(tmpdir, "src/pages/dashboard/Page.tsx", PAGE_TSX_CONTENT)
    seed_file(tmpdir, "src/pages/dashboard/components/KpiRow.tsx", KPI_ROW_TSX_CONTENT)
    seed_file(tmpdir, "src/pages/dashboard/components/Timeseries.tsx", TIMESERIES_TSX_CONTENT)
    seed_file(tmpdir, "src/pages/dashboard/components/LineChart.tsx", LINECHART_TSX_CONTENT)
    _seed_and_commit(tmpdir, "seed full workspace")
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def create_workspace():
    """Workspace with Page + LineChart, NO KpiRow — CREATE will bring it back."""
    tmpdir = _init_git_workspace("create_")
    seed_file(tmpdir, "src/pages/dashboard/Page.tsx", PAGE_TSX_CONTENT)
    seed_file(tmpdir, "src/pages/dashboard/components/LineChart.tsx", LINECHART_TSX_CONTENT)
    _seed_and_commit(tmpdir, "seed create workspace")
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def replace_workspace():
    """Workspace with Page + Timeseries + LineChart, NO KpiRow — REPLACE creates it."""
    tmpdir = _init_git_workspace("replace_")
    seed_file(tmpdir, "src/pages/dashboard/Page.tsx", PAGE_TSX_CONTENT)
    seed_file(tmpdir, "src/pages/dashboard/components/Timeseries.tsx", TIMESERIES_TSX_CONTENT)
    seed_file(tmpdir, "src/pages/dashboard/components/LineChart.tsx", LINECHART_TSX_CONTENT)
    _seed_and_commit(tmpdir, "seed replace workspace")
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
