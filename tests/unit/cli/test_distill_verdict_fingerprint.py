"""Regression: the inherited distill verdict must not read as a config change.

``repowise update`` backfills a workspace member's ``distill.commands.enabled``
verdict into ``.repowise/config.yaml`` at the start of every run. When init
left the verdict unwritten, the first update's backfill changed the config
fingerprint computed at init time, and the update silently replaced the
incremental path with a full health re-score of every file (47s instead of
the partial update on an ~900-file Go repo). Init now runs the same backfill
before fingerprinting, so the update-time backfill is a no-op.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from repowise.cli.commands.workspace_cmd import inherit_workspace_distill_verdict
from repowise.cli.helpers import config_fingerprint
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig


def _make_workspace(tmp_path: Path, *, primary_verdict: bool | None) -> Path:
    """Workspace root with a primary repo and a member repo; returns member."""
    primary = tmp_path / "primary"
    member = tmp_path / "member"
    (primary / ".repowise").mkdir(parents=True)
    (member / ".repowise").mkdir(parents=True)
    if primary_verdict is not None:
        (primary / ".repowise" / "config.yaml").write_text(
            yaml.dump({"distill": {"commands": {"enabled": primary_verdict}}}),
            encoding="utf-8",
        )
    WorkspaceConfig(
        repos=[
            RepoEntry(path="primary", alias="primary", is_primary=True),
            RepoEntry(path="member", alias="member"),
        ],
        default_repo="primary",
    ).save(tmp_path)
    return member


class TestDistillVerdictFingerprint:
    def test_inherit_is_idempotent_for_fingerprint(self, tmp_path: Path) -> None:
        """Once the verdict is written (as init now does), re-running the
        update-time backfill leaves the config fingerprint unchanged."""
        member = _make_workspace(tmp_path, primary_verdict=True)

        # Init-time backfill, then fingerprint (the new init ordering).
        inherit_workspace_distill_verdict(member)
        fp_at_init = config_fingerprint(member)

        # Update-time backfill must be a no-op.
        inherit_workspace_distill_verdict(member)
        assert config_fingerprint(member) == fp_at_init

        cfg = yaml.safe_load((member / ".repowise" / "config.yaml").read_text())
        assert cfg["distill"]["commands"]["enabled"] is True

    def test_unwritten_verdict_changed_the_fingerprint(self, tmp_path: Path) -> None:
        """The bug shape: fingerprint taken before the backfill differs from
        the fingerprint after it, which is what made the first update of
        every workspace member take the full-rescore path."""
        member = _make_workspace(tmp_path, primary_verdict=True)
        fp_without_verdict = config_fingerprint(member)
        inherit_workspace_distill_verdict(member)
        assert config_fingerprint(member) != fp_without_verdict

    def test_no_primary_verdict_is_noop(self, tmp_path: Path) -> None:
        member = _make_workspace(tmp_path, primary_verdict=None)
        fp_before = config_fingerprint(member)
        inherit_workspace_distill_verdict(member)
        assert config_fingerprint(member) == fp_before
        assert not (member / ".repowise" / "config.yaml").exists()

    def test_outside_workspace_is_noop(self, tmp_path: Path) -> None:
        lone = tmp_path / "lone"
        (lone / ".repowise").mkdir(parents=True)
        fp_before = config_fingerprint(lone)
        inherit_workspace_distill_verdict(lone)
        assert config_fingerprint(lone) == fp_before
