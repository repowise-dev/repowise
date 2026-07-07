"""Repo-relative baseline sampling for change-risk percentiles.

Scores a repo's recent commits so a single change's raw risk score can be
ranked against them (see :mod:`.normalize`). Lives in core (not the CLI) so
both the CLI and the server can build a percentile off the same live-git
sample without duplicating the walk.
"""

from __future__ import annotations

import subprocess

from .features import features_from_file_changes
from .model import score_change


def baseline_scores(
    repo_path: str,
    anchor: str,
    limit: int,
    extensions: tuple[str, ...],
    exclude: str,
) -> list[float]:
    """Score the repo's recent commits to build a local risk distribution.

    One ``git log --numstat`` call (no per-commit author lookup), so it stays
    cheap enough for a pre-merge gate. Experience is left unknown for the
    baseline; the target is ranked with experience likewise unknown, so the
    comparison is like-with-like: a diff-shape percentile within this repo.
    """
    out = subprocess.run(
        ["git", "log", f"-n{limit}", "--no-merges", "--format=%x1e%H", "--numstat", anchor],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout

    scores: list[float] = []
    for block in out.split("\x1e"):
        lines = block.strip().split("\n")
        if not lines or not lines[0]:
            continue
        sha, rows = lines[0].strip(), lines[1:]
        # Drop the target itself from its own baseline (short or full sha).
        if exclude and (sha.startswith(exclude) or exclude.startswith(sha)):
            continue
        changes: list[tuple[str, int, int]] = []
        for row in rows:
            parts = row.split("\t")
            if len(parts) != 3:
                continue
            a_raw, d_raw, path = parts
            if extensions and not path.endswith(extensions):
                continue
            a = int(a_raw) if a_raw.isdigit() else 0
            d = int(d_raw) if d_raw.isdigit() else 0
            changes.append((path, a, d))
        if not changes:
            continue
        feats = features_from_file_changes(changes, exp=None)
        scores.append(score_change(feats).score)
    return scores
