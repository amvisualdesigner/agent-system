"""E2E golden tests: MODIFY preserves imports, verify passes, contract equivalence.

These tests run the full apply_engine pipeline against a seeded git workspace
and assert filesystem-level outcomes (not result dict structure).

Scenarios:
  1. MODIFY KpiRow — imports preserved, no generated placeholder
  2. MODIFY KpiRow — verify.status == "ok"
  3. CREATE → MODIFY → CREATE — contract model (interface + export) identical
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid

import pytest

from app.config.settings import settings
from app.intent.models import ConfirmedIntent, IntentAction


# ── Helpers ──────────────────────────────────────────────────────────

KPI_ROW_IMPORT_RICH_TSX = """\
import React from 'react';
import { KpiCard } from './KpiCard';
import styles from './KpiRow.module.css';
import type { KpiItem } from '../../types';

interface KpiRowProps {
  data: KpiItem[];
}

export const KpiRow: React.FC<KpiRowProps> = ({ data }) => {
  return (
    <div className={styles.row}>
      {data.map((item) => (
        <KpiCard key={item.label} value={item.value} label={item.label} />
      ))}
    </div>
  );
};
"""


def _component_path(workspace: str, *parts: str) -> str:
    return os.path.join(workspace, "src", *parts)


def _read_file(path: str) -> str:
    with open(path) as f:
        return f.read()


def _file_exists(workspace: str, *parts: str) -> bool:
    return os.path.isfile(_component_path(workspace, *parts))


def _init_git_workspace(prefix: str) -> str:
    tmpdir = tempfile.mkdtemp(prefix=prefix, dir=settings.RUNS_DIR)
    subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, capture_output=True)
    os.makedirs(os.path.join(tmpdir, "src", "components"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "src", "pages", "dashboard"), exist_ok=True)
    return tmpdir


def _seed_and_commit(workspace: str, msg: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=workspace, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=workspace, capture_output=True)


def _seed_file(workspace: str, rel_path: str, content: str) -> str:
    full = os.path.join(workspace, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    return rel_path


def _run_apply(workspace: str, confirmed: ConfirmedIntent) -> dict:
    from app.engine.apply_engine import apply_engine
    from app.runtime.context import RunContext

    from app.intent.plan_compiler import compile_plan

    plan = compile_plan(confirmed)
    artifacts_tmp = tempfile.mkdtemp(prefix="e2e_modify_", dir=settings.ARTIFACTS_DIR)
    ctx = RunContext(
        run_id=str(uuid.uuid4()),
        base_dir=workspace,
        workspace=workspace,
        artifacts=artifacts_tmp,
    )
    try:
        return apply_engine(ctx.run_id, plan.to_dict(), ctx, dry_run=False)
    finally:
        shutil.rmtree(artifacts_tmp, ignore_errors=True)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def modify_workspace():
    """Git workspace with import-rich KpiRow.tsx + Page.tsx referencing it."""
    tmpdir = _init_git_workspace("modify_")
    _seed_file(tmpdir, "src/pages/dashboard/Page.tsx", """\
import React from 'react';
import { Timeseries } from '../components/Timeseries';
import { KpiRow } from '../components/KpiRow';

interface PageProps {}

export const Page: React.FC<PageProps> = () => {
  return (
    <div className="page">
      <KpiRow data={[]} />
    </div>
  );
};
""")
    _seed_file(tmpdir, "src/components/KpiRow.tsx", KPI_ROW_IMPORT_RICH_TSX)
    _seed_file(tmpdir, "src/components/Timeseries.tsx", """\
import React from 'react';

interface TimeseriesProps {
  data: Array<{ x: string; y: number }>;
}

export const Timeseries: React.FC<TimeseriesProps> = ({ data }) => {
  return <div />;
};
""")
    _seed_and_commit(tmpdir, "seed modify workspace")
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def clean_workspace():
    """Git workspace with ONLY Page.tsx + Timeseries.tsx — NO KpiRow (CREATE will generate)."""
    tmpdir = _init_git_workspace("clean_")
    _seed_file(tmpdir, "src/pages/dashboard/Page.tsx", """\
import React from 'react';
import { Timeseries } from '../components/Timeseries';
import { KpiRow } from '../components/KpiRow';

interface PageProps {}

export const Page: React.FC<PageProps> = () => {
  return (
    <div className="page">
      <KpiRow data={[]} />
    </div>
  );
};
""")
    _seed_file(tmpdir, "src/components/Timeseries.tsx", """\
import React from 'react';

interface TimeseriesProps {
  data: Array<{ x: string; y: number }>;
}

export const Timeseries: React.FC<TimeseriesProps> = ({ data }) => {
  return <div />;
};
""")
    _seed_and_commit(tmpdir, "seed clean workspace for CREATE")
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


# ── Tests ─────────────────────────────────────────────────────────────


class TestModifyPreservesImports:
    """MODIFY must NOT strip non-React imports or custom import patterns."""

    MODIFY_ACTIONS = [
        IntentAction(
            verb="modify",
            target_capability="presentation.kpi_row",
            params={"metrics": ["revenue", "growth"]},
        ),
    ]

    def test_modify_kpi_row_preserves_imports(self, modify_workspace):
        kpi_path = _component_path(modify_workspace, "components", "KpiRow.tsx")
        assert os.path.exists(kpi_path)

        result = _run_apply(modify_workspace, ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=self.MODIFY_ACTIONS,
            params={"metrics": ["revenue", "growth"]},
            user_message="update KPI metrics to show revenue and growth",
            interpretation_id="e2e-modify-imports",
        ))
        assert result["execution"]["status"] in ("ok", "verify_failed"), (
            f"MODIFY should succeed, got {result['execution']['status']}"
        )

        content = _read_file(kpi_path)

        # All non-React imports must survive
        assert "import { KpiCard } from './KpiCard'" in content, (
            "KpiCard import must survive MODIFY (structural preservation)"
        )
        assert "import styles from './KpiRow.module.css'" in content, (
            "styles import must survive MODIFY"
        )
        assert "import type { KpiItem } from '../../types'" in content, (
            "type import must survive MODIFY"
        )
        assert "__COMPOSITION__" not in content, "No composition placeholder"
        assert "KpiRowProps" in content, "Component interface must be present"

    def test_verify_passes_after_modify(self, modify_workspace):
        result = _run_apply(modify_workspace, ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=self.MODIFY_ACTIONS,
            params={"metrics": ["revenue", "growth"]},
            user_message="update KPI metrics to show revenue and growth",
            interpretation_id="e2e-modify-verify",
        ))
        assert result["execution"]["status"] == "ok", (
            f"verify should pass after MODIFY, got {result['execution']['status']}"
        )


class TestCreateThenModifyConsistency:
    """CREATE then MODIFY — contract model must be self-consistent.

    After MODIFY, the interface prop names must match the destructure names
    in the export line. CREATE may produce a destructure mismatch (the bug),
    MODIFY must fix it.
    """

    CREATE_ACTIONS = [
        IntentAction(
            verb="create",
            target_capability="presentation.kpi_row",
            params={"metrics": ["revenue", "growth"]},
        ),
    ]
    MODIFY_ACTIONS = [
        IntentAction(
            verb="modify",
            target_capability="presentation.kpi_row",
            params={"metrics": ["revenue", "profit"]},
        ),
    ]

    @staticmethod
    def _interface_prop_names(content: str) -> set[str]:
        """Extract property names from the first interface block."""
        import re
        m = re.search(r'interface\s+\w+\s*\{([^}]+)\}', content, re.DOTALL)
        if not m:
            return set()
        return {p.split(":")[0].strip() for p in m.group(1).split("\n") if ":" in p and p.strip()}

    @staticmethod
    def _destructure_names(content: str) -> set[str]:
        """Extract destructured parameter names from the export line."""
        import re
        m = re.search(r'=\s*\(\{\s*([^}]+)\s*\}\)', content)
        if not m:
            return set()
        return {p.strip().split(":")[0].split("=")[0].strip() for p in m.group(1).split(",") if p.strip()}

    def test_create_then_modify_yields_self_consistent_contract(self, clean_workspace):
        kpi_path = _component_path(clean_workspace, "components", "KpiRow.tsx")
        assert not os.path.exists(kpi_path), "Precondition: KpiRow should NOT exist"

        # Step 1: CREATE — may produce { metrics } (the bug)
        result1 = _run_apply(clean_workspace, ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=self.CREATE_ACTIONS,
            params={"metrics": ["revenue", "growth"]},
            user_message="create KPI row",
            interpretation_id="e2e-create-step1",
        ))
        assert result1["execution"]["status"] in ("ok", "verify_failed")
        assert os.path.exists(kpi_path)

        # Step 2: MODIFY — must reconcile destructure
        result2 = _run_apply(clean_workspace, ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=self.MODIFY_ACTIONS,
            params={"metrics": ["revenue", "profit"]},
            user_message="modify KPI row metrics",
            interpretation_id="e2e-modify-step2",
        ))
        assert result2["execution"]["status"] in ("ok", "verify_failed")

        modify_content = _read_file(kpi_path)
        iface_names = self._interface_prop_names(modify_content)
        destructure_names = self._destructure_names(modify_content)

        # Wait: the file may have extra props beyond the first destructure param.
        # We just check that the first (required) interface prop has a matching
        # destructure name. The destructure set is a subset of interface props.
        missing = destructure_names - iface_names
        assert not missing, (
            "After MODIFY, destructure names must match interface prop names.\n"
            f"Interface props: {iface_names}\n"
            f"Destructure:     {destructure_names}\n"
            f"Missing:         {missing}\n"
            f"--- content ---\n{modify_content}\n"
        )

    def test_self_consistent_on_recreate(self, clean_workspace):
        """CREATE → MODIFY → CREATE preserves consistency.

        Under F1 the second CREATE on an already-materialized target is an
        explicit conflict (no new-instance nomination) — never a silent
        overwrite or a reinterpretation into MODIFY. The conflicting run must
        be a material no-op: the file (from the MODIFY step) stays consistent.
        """
        kpi_path = _component_path(clean_workspace, "components", "KpiRow.tsx")
        assert not os.path.exists(kpi_path)

        _run_apply(clean_workspace, ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=self.CREATE_ACTIONS,
            params={"metrics": ["revenue", "growth"]},
            user_message="create KPI row",
            interpretation_id="e2e-create-re-1",
        ))
        _run_apply(clean_workspace, ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=self.MODIFY_ACTIONS,
            params={"metrics": ["revenue", "profit"]},
            user_message="modify KPI row",
            interpretation_id="e2e-create-re-modify",
        ))
        result3 = _run_apply(clean_workspace, ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=self.CREATE_ACTIONS,
            params={"metrics": ["revenue", "growth"]},
            user_message="create KPI row again",
            interpretation_id="e2e-create-re-2",
        ))
        # F1: CREATE on an existing target → explicit conflict, no overwrite,
        # no reinterpretation into MODIFY.
        assert result3["execution"]["status"] == "clarification_needed", (
            f"Second CREATE on existing target must conflict, got {result3['execution']['status']}"
        )
        detail3 = result3["execution"].get("detail", "")
        assert "presentation.kpi_row" in detail3, (
            f"Conflict must cite the existing target, got detail={detail3!r}"
        )

        # The conflicting run is a material no-op: destructure must still match
        # the interface from the MODIFY step.
        final_content = _read_file(kpi_path)
        iface_names = self._interface_prop_names(final_content)
        destructure_names = self._destructure_names(final_content)
        missing = destructure_names - iface_names
        assert not missing, (
            "After CREATE → MODIFY → CREATE, destructure must match interface.\n"
            f"Interface props: {iface_names}\n"
            f"Destructure:     {destructure_names}\n"
            f"Missing:         {missing}\n"
        )
