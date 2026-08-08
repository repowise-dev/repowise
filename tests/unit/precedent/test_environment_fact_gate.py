"""The one check that describes the machine must stay opt-in.

``_run_ingestion`` is the *full pipeline*, not ``init``: the workspace updater
falls back to it, and so does the hosted job executor. A formatter check run
there measures the indexer's container and stores the answer as a fact about
the user's repository, which is the failure mode this gate exists to prevent.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from repowise.core.pipeline.orchestrator import run_pipeline
from repowise.core.pipeline.phases.ingestion import _run_ingestion, reparse_for_resume


def _default(fn, name: str):
    return inspect.signature(fn).parameters[name].default


def test_pipeline_entry_points_default_to_off() -> None:
    for fn in (run_pipeline, _run_ingestion, reparse_for_resume):
        assert _default(fn, "derive_environment_facts") is False, fn.__qualname__


def test_only_the_local_init_command_opts_in() -> None:
    """Anything else that turns this on has to justify itself here first."""
    cli = Path(__file__).resolve().parents[3] / "packages" / "cli" / "src"
    core = Path(__file__).resolve().parents[3] / "packages" / "core" / "src"
    server = Path(__file__).resolve().parents[3] / "packages" / "server" / "src"
    opted_in = {
        py.relative_to(py.parents[4]).as_posix()
        for root in (cli, core, server)
        for py in root.rglob("*.py")  # tests may rglob; src may not
        if "derive_environment_facts=True" in py.read_text(encoding="utf-8", errors="ignore")
    }
    assert opted_in == {
        "repowise/cli/commands/init_cmd/command.py",
        "repowise/cli/commands/init_cmd/workspace.py",
    }, opted_in
