"""Prompts in `repowise init` that must degrade rather than end the run.

Agents set repowise up on a user's behalf, usually as `repowise init --yes` or
with stdin pointed at /dev/null. `sys.stdin.isatty()` is not a reliable guard:
on Windows under Git Bash, `init < /dev/null` reports a terminal and then reads
EOF on the first question.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import click

from repowise.cli import cost_gate
from repowise.cli.commands.init_cmd._interactive import offer_hook_install


def _est(usd: float) -> SimpleNamespace:
    """A stand-in for a cost estimate, which the gate only reads two fields of."""
    return SimpleNamespace(estimated_cost_usd=usd, cost_range=None, is_calibrated=False)


class TestCostGate:
    def test_below_threshold_never_prompts(self, monkeypatch):
        """A cheap run is not worth interrupting, so the gate stays out of the way."""

        def _boom(*_a, **_k):
            raise AssertionError("prompted below the cost gate threshold")

        monkeypatch.setattr(click, "confirm", _boom)
        assert cost_gate.cost_gate_declined(_est(0.10), yes=False, message="m") is False

    def test_yes_never_prompts(self, monkeypatch):
        """--yes means the spend is pre-approved."""

        def _boom(*_a, **_k):
            raise AssertionError("prompted under --yes")

        monkeypatch.setattr(click, "confirm", _boom)
        assert cost_gate.cost_gate_declined(_est(999.0), yes=True, message="m") is False

    def test_non_tty_declines_without_prompting(self, monkeypatch):
        """No terminal means no answer. Decline, do not Abort.

        The gate is the only prompt that fires *after* a full index, so an
        Abort here throws away every minute already spent. Declining hands the
        caller back to the template renderer with the index intact.
        """
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)

        def _boom(*_a, **_k):
            raise AssertionError("prompted with no terminal attached")

        monkeypatch.setattr(click, "confirm", _boom)
        assert cost_gate.cost_gate_declined(_est(50.0), yes=False, message="m") is True

    def test_default_is_yes_under_the_hard_ceiling(self, monkeypatch):
        """The user just configured this run; Enter-through continues it."""
        seen: dict = {}
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr(
            click, "confirm", lambda _m, default=None: seen.setdefault("default", default) or True
        )
        cost_gate.confirm_cost_gate("m", estimated_usd=5.0)
        assert seen["default"] is True

    def test_default_flips_above_the_hard_ceiling(self, monkeypatch):
        """A bill this size must be typed, not defaulted into."""
        seen: dict = {}
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)

        def _confirm(_m, default=None):
            seen["default"] = default
            return False

        monkeypatch.setattr(click, "confirm", _confirm)
        cost_gate.confirm_cost_gate("m", estimated_usd=cost_gate.COST_GATE_HARD_USD + 1)
        assert seen["default"] is False


class TestHookOfferDegrades:
    def test_eof_on_hook_offer_does_not_raise(self, monkeypatch, tmp_path):
        """A finished run must not fail on an optional trailing question."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr(
            "repowise.cli.commands.init_cmd._interactive.click.confirm",
            lambda *_a, **_k: (_ for _ in ()).throw(click.Abort()),
        )
        monkeypatch.setattr("repowise.cli.hooks.status", lambda _p: "missing")
        # Must not raise.
        offer_hook_install(
            SimpleNamespace(print=lambda *_a, **_k: None), [tmp_path], None, yes=False
        )
