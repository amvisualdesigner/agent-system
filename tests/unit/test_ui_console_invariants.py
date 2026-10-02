"""Fase 5 — static invariants of the operator console (ui/index.html).

The console is the human-decision surface, so these checks lock the wiring
that the browser cannot be trusted to negotiate at runtime:
  * every `onclick` target must exist (no dead buttons);
  * no value may be interpolated into an inline JS string literal;
  * the confirm payload must carry the frozen human selection;
  * conflict → retry / cancel must hit real endpoints.
"""

import pathlib
import re

UI_PATH = pathlib.Path(__file__).resolve().parents[2] / "ui" / "index.html"
HTML = UI_PATH.read_text(encoding="utf-8")
SCRIPT = "\n".join(re.findall(r"<script>(.*?)</script>", HTML, re.S))


def _top_level_definitions() -> set:
    fns = set(re.findall(r"^(?:async\s+)?function\s+(\w+)\s*\(", SCRIPT, re.M))
    fns |= set(re.findall(r"^\s*(?:async\s+)?function\s+(\w+)\s*\(", SCRIPT, re.M))
    return fns


class TestNoDeadHandlers:
    def test_every_onclick_target_exists(self):
        targets = set()
        for call in re.findall(r'onclick="([a-zA-Z_]\w*)\(', HTML):
            targets.add(call)
        missing = sorted(t for t in targets if t not in _top_level_definitions())
        assert missing == [], f"onclick handlers without definition: {missing}"

    def test_handlers_are_referenced_somewhere(self):
        dead = []
        for fn in sorted(_top_level_definitions()):
            if fn.startswith("on") or fn in ("connectSSE",):
                if f"{fn}(" not in HTML:
                    dead.append(fn)
        assert dead == [], f"unused SSE handlers: {dead}"


class TestNoInlineJsInterpolation:
    """`onclick="f('${v}')"` breaks on any value containing a quote."""

    def test_no_quoted_template_interpolation_in_onclick(self):
        # Numeric index interpolation (`f(${i})`) is safe; interpolation into a
        # quoted JS literal (`f('${v}')`) breaks on any value containing a quote.
        offenders = re.findall(r"""onclick="[^"]*['"]\$\{""", HTML)
        assert offenders == [], f"inline JS string interpolation in onclick: {offenders}"

    def test_choice_dispatch_is_numeric_registry(self):
        assert "registerInstanceChoice(" in SCRIPT
        assert "registerPageChoice(" in SCRIPT
        assert re.search(r"onclick=\"handleInstanceChoice\(\$\{regIdx\},this\)\"", HTML)
        assert re.search(r"onclick=\"handlePageChoice\(\$\{regIdx\}, this\)\"", HTML)


class TestNoDuplicateTopLevelBindings:
    def test_singletons_declared_once(self):
        for name in ("selectedInstances", "instanceChoiceCandidates",
                     "pageChoiceCandidates", "runId", "currentInterpretation",
                     "currentPageChoice"):
            decls = re.findall(rf"^let {name}\b", SCRIPT, re.M)
            assert len(decls) == 1, f"{name} declared {len(decls)} times"


class TestConfirmPayload:
    def test_freezes_instance_selection(self):
        assert "action.instance_hint = hint;" in SCRIPT

    def test_carries_source_capability(self):
        assert "a.source_capability" in SCRIPT
        assert "action.source_capability = a.source_capability;" in SCRIPT

    def test_carries_attach(self):
        assert "action.attach = a.attach;" in SCRIPT

    def test_sends_page_context_choice(self):
        assert "payload.page_context_choice = currentPageChoice;" in SCRIPT


class TestRecoveryEndpoints:
    def test_retry_hits_real_endpoint(self):
        assert "${ORCH}/run/${runId}/retry" in SCRIPT

    def test_cancel_hits_real_endpoint(self):
        assert "${ORCH}/run/${runId}/cancel" in SCRIPT

    def test_conflict_renders_recovery_actions(self):
        assert "function onConflictResult(" in SCRIPT
        assert "return onConflictResult(exec, data);" in SCRIPT
        block = SCRIPT.split("function onConflictResult(")[1].split("\nfunction ")[0]
        assert "handleRetry(this)" in block
        assert "handleCancel(this)" in block

    def test_conflict_explains_plan_is_untouched(self):
        block = SCRIPT.split("function onConflictResult(")[1].split("\nfunction ")[0]
        assert "NO cambia" in block

    def test_conflict_result_not_finalized_as_success(self):
        block = SCRIPT.split('case "result":')[1].split("case \"error\"")[0]
        assert '"conflict"' in block
        assert '"confirmed"' in block
        assert '"cancelled"' in block


class TestSemanticAlternatives:
    def test_renders_only_when_ambiguous(self):
        block = SCRIPT.split("// ── Semantic ambiguity")[1].split("// ── Instance ambiguity")[0]
        assert "alternatives.length > 1" in block
        assert "data-alt-idx" in block

    def test_selection_dispatch_is_numeric(self):
        assert "function handleAlternativeChoice(" in SCRIPT
        assert re.search(r'onclick="handleAlternativeChoice\(\$\{i\},this\)"', HTML)

    def test_sends_alternative_index(self):
        assert "payload.alternative_index = selectedAlternativeIndex;" in SCRIPT

    def test_selection_is_reset_per_run(self):
        assert "selectedAlternativeIndex = null;" in SCRIPT

    def test_distinct_from_instance_and_page_context(self):
        # three independent human-decision surfaces
        assert "handleInstanceChoice" in SCRIPT
        assert "handlePageChoice" in SCRIPT
        assert "handleAlternativeChoice" in SCRIPT


class TestVocabularyAlignment:
    def test_uses_backend_preplan_status(self):
        assert "clarification_needed" in SCRIPT
        assert "needs_clarification" not in SCRIPT

    def test_no_legacy_awaiting_apply(self):
        assert "awaiting_apply" not in SCRIPT