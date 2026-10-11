"""Regression tests for issue #1484: the Function Hotspot gate must be scored
against the full-repo p80 even on incremental runs.

On an incremental run ``walked``/``target_files`` holds only the changed files,
so deriving ``repo_function_mod_p80`` from them biases the gate toward the
churn-heavy subset (e.g. full-repo ``[1,2,2,3,4,4,5]`` → p80=4 but changed
subset ``[4,4,5]`` → p80=5), which flips a file's hotspot verdict between
``init`` and ``update``. The incremental caller now loads the persisted
full-repo percentile (``git_function_blame`` rollup) and passes it in as an
override.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.analysis.health.engine import HealthAnalyzer


def _parsed_file(path: str) -> SimpleNamespace:
    return SimpleNamespace(
        file_info=SimpleNamespace(
            path=path,
            language="python",
            abs_path=f"/tmp/{path}",
            is_test=False,
        ),
        symbols=[],
    )


class _CaptureEvaluator(HealthAnalyzer):
    """Engine whose ``_evaluate_file`` records the p80 override instead of
    doing real biomarker work."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.captured_p80s: list[int | None] = []

    def _evaluate_file(self, *args, **kwargs):
        self.captured_p80s.append(kwargs.get("repo_function_mod_p80"))
        pf = args[0]
        return SimpleNamespace(file_path=pf.file_info.path, nloc=0, score=10.0), [], []


def test_incremental_analyze_uses_override_not_walked_subset():
    """``analyze(changed_files=..., repo_function_mod_p80=X)`` must hand the
    override to the per-file evaluation, not the value derived from the
    changed subset (issue #1484)."""
    eng = _CaptureEvaluator(
        graph=None,
        git_meta_map={
            "src/a.py": {},
            "src/b.py": {},
        },
        parsed_files=[_parsed_file("src/a.py"), _parsed_file("src/b.py")],
    )
    eng.analyze(changed_files=["src/a.py"], repo_function_mod_p80=4)
    assert eng.captured_p80s and eng.captured_p80s == [4]


def test_full_analyze_still_derives_p80_from_walked():
    """A full run (no ``changed_files``) keeps deriving the percentile from the
    walked set — the override is only an incremental-run input."""
    eng = _CaptureEvaluator(
        graph=None,
        git_meta_map={"src/a.py": {}},
        parsed_files=[_parsed_file("src/a.py")],
    )
    eng.analyze()
    # No blame index on any walked file → p80 is None (the "no signal"
    # outcome), proving the default computation path still ran.
    assert eng.captured_p80s and eng.captured_p80s == [None]


def test_analyze_async_forwards_override():
    import asyncio

    eng = _CaptureEvaluator(
        graph=None,
        git_meta_map={"src/a.py": {}},
        parsed_files=[_parsed_file("src/a.py")],
    )
    asyncio.run(eng.analyze_async(changed_files=["src/a.py"], repo_function_mod_p80=4))
    assert eng.captured_p80s and eng.captured_p80s == [4]
