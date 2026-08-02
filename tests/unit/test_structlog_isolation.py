"""The global structlog config does not leak from one test into the next.

``silence_logs_for_machine_output`` and ``configure_cli_logging`` install a
filtering bound logger at ERROR for the whole process. Without the autouse
fixture in ``tests/conftest.py`` that setting outlives the test that made it,
and every later ``capture_logs`` assertion on an ``info`` or ``warning`` reads
an empty list. Such a test passes in isolation and fails in a full run.

The two tests below must stay in this order: the first one does the damage,
the second one proves it was undone.
"""

from __future__ import annotations

import structlog
from structlog.testing import capture_logs

from repowise.cli.helpers import silence_logs_for_machine_output


def test_a_cli_command_silences_logs_process_wide() -> None:
    silence_logs_for_machine_output()
    with capture_logs() as logs:
        structlog.get_logger(__name__).warning("silenced_here")
    assert logs == []


def test_b_the_next_test_can_still_capture_a_warning() -> None:
    with capture_logs() as logs:
        structlog.get_logger(__name__).warning("visible_again")
    assert [entry["event"] for entry in logs] == ["visible_again"]
