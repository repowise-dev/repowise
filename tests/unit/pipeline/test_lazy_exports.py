"""The pipeline package resolves every public name without loading it eagerly."""

from __future__ import annotations

import subprocess
import sys

import repowise.core.pipeline as pipeline


def test_every_exported_name_resolves() -> None:
    for name in pipeline.__all__:
        assert getattr(pipeline, name) is not None, name


def test_the_phase_timer_does_not_load_the_generation_stack() -> None:
    probe = (
        "import sys; from repowise.core.pipeline import PhaseTimings, timed; "
        "print('repowise.core.pipeline.orchestrator' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == "False"
