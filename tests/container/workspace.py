"""Workspace fixtures for container-dependent tests.

Creates a temporary directory on the host seeded with fixture TSX files.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import pytest


def _seed_file(workspace: str, rel_path: str, content: str = "") -> str:
    full = os.path.join(workspace, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    if not os.path.isfile(full):
        with open(full, "w") as f:
            f.write(content)
    return rel_path


SALES_PAGE_TSX = """\
import { KpiRow } from './KpiRow';
import { Timeseries } from './Timeseries';
import { LineChart } from './LineChart';
import { BarChart } from './BarChart';
import { DataTable } from './DataTable';
import { useDashboardData } from './useDashboardData';

export function SalesOverviewPage() {
  const { kpiData, chartData, tableData, isLoading } = useDashboardData();
  if (isLoading) return <div>Loading...</div>;
  return (
    <div className="page">
      <h1>Sales Overview</h1>
      <KpiRow data={kpiData} />
      <div className="charts">
        <LineChart data={chartData.line} />
        <BarChart data={chartData.bar} />
        <Timeseries data={chartData.timeseries} />
      </div>
      <DataTable columns={tableData.columns} data={tableData.rows} />
    </div>
  );
}
"""

KPI_ROW_TSX = """\
interface KpiItem {
  label: string;
  value: string | number;
  trend?: 'up' | 'down';
}

interface KpiRowProps {
  data: KpiItem[];
}

export function KpiRow({ data }: KpiRowProps) {
  return (
    <div className="kpi-row">
      {data.map((item, i) => (
        <div key={i} className="kpi-card">
          <span className="label">{item.label}</span>
          <span className="value">{item.value}</span>
        </div>
      ))}
    </div>
  );
}
"""

TIMESERIES_TSX = """\
type Point = {
  x: string | number;
  y: number;
};

type Props = {
  data?: Point[];
  title?: string;
};

export function Timeseries({ data = [], title = "Trend" }: Props) {
  const width = 400;
  const height = 200;
  const padding = 20;

  if (!data.length) {
    return (
      <div>
        <div className="title">{title}</div>
        <div className="empty">No data</div>
      </div>
    );
  }

  const scaleX = (i: number) =>
    padding + (i / (data.length - 1)) * (width - padding * 2);
  const scaleY = (v: number) =>
    padding + (1 - (v - Math.min(...data.map(d => d.y))) / (Math.max(...data.map(d => d.y)) - Math.min(...data.map(d => d.y)) || 1)) * (height - padding * 2);

  return (
    <div>
      <div className="title">{title}</div>
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet">
        <polyline fill="none" stroke="black" strokeWidth="2"
          points={data.map((p, i) => `${scaleX(i)},${scaleY(p.y)}`).join(" ")}
        />
        {data.map((point, i) => (
          <circle key={i} cx={scaleX(i)} cy={scaleY(point.y)} r={3} fill="black" />
        ))}
      </svg>
    </div>
  );
}
"""


@pytest.fixture
def container_workspace():
    """Create a temp workspace seeded with standard fixtures.

    Yields host path.
    """
    tmpdir = tempfile.mkdtemp(prefix="container_e2e_")
    _seed_file(tmpdir, "SalesOverviewPage.tsx", SALES_PAGE_TSX)
    _seed_file(tmpdir, "KpiRow.tsx", KPI_ROW_TSX)
    _seed_file(tmpdir, "Timeseries.tsx", TIMESERIES_TSX)

    subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmpdir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial", "--allow-empty"],
        cwd=tmpdir, capture_output=True,
    )

    yield tmpdir

    shutil.rmtree(tmpdir, ignore_errors=True)
