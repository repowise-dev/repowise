"""Unit tests for the just-in-time change-risk model + feature extraction."""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

import pytest

from repowise.core.analysis.change_risk import (
    ChangeFeatures,
    ChangeRiskResult,
    change_risk_payload,
    extract_commit_features,
    extract_range_features,
    features_from_file_changes,
    score_change,
    score_live_change,
    working_tree_is_dirty,
)
from repowise.core.analysis.change_risk.model import _CONSTANTS, _sigmoid


def _feat(**kw) -> ChangeFeatures:
    base = dict(la=0, ld=0, nf=0, nd=0, ns=0, entropy=0.0, exp=0)
    base.update(kw)
    return ChangeFeatures(**base)


# ---------------------------------------------------------------------------
# Model mechanics — linear, attributable, bounded.
# ---------------------------------------------------------------------------


def test_score_is_bounded_and_levelled() -> None:
    risk = score_change(_feat(la=200, ld=120, nf=30, nd=12, ns=6, entropy=4.0, exp=0))
    assert 0.0 <= risk.score <= 10.0
    assert risk.level in {"low", "moderate", "high"}


def test_logit_is_exact_sum_of_driver_contributions() -> None:
    """The reported per-driver contributions must reconstruct the model output
    exactly — the attribution is the model, not a post-hoc approximation."""
    f = _feat(la=50, ld=10, nf=5, nd=3, ns=2, entropy=2.0, exp=8)
    risk = score_change(f)
    logit = float(_CONSTANTS["intercept"]) + sum(d.contribution for d in risk.drivers)
    assert risk.score == pytest.approx(round(10.0 * _sigmoid(logit), 1))


def test_larger_diff_scores_higher() -> None:
    small = score_change(_feat(la=5, ld=1, nf=1, nd=1, ns=1, entropy=0.0, exp=50))
    large = score_change(_feat(la=400, ld=200, nf=25, nd=10, ns=5, entropy=4.0, exp=50))
    assert large.score > small.score


def test_author_experience_is_protective() -> None:
    """Holding the diff fixed, a more experienced author is lower risk (the
    calibrated `exp` coefficient is negative — literature-consistent)."""
    base = dict(la=80, ld=40, nf=8, nd=4, ns=2, entropy=3.0)
    newcomer = score_change(_feat(**base, exp=0))
    veteran = score_change(_feat(**base, exp=2000))
    assert veteran.score <= newcomer.score
    exp_driver = next(d for d in veteran.drivers if d.feature == "exp")
    assert exp_driver.contribution < 0  # protective push


def test_unknown_experience_is_neutral() -> None:
    """exp=None (diff-only caller) contributes zero — no imputed inexperience."""
    base = dict(la=80, ld=40, nf=8, nd=4, ns=2, entropy=3.0)
    risk = score_change(_feat(**base, exp=None))
    exp_driver = next(d for d in risk.drivers if d.feature == "exp")
    assert exp_driver.contribution == 0.0
    # Identical to scoring with exp omitted from the logit entirely.
    from repowise.core.analysis.change_risk.model import _CONSTANTS, _sigmoid

    logit = float(_CONSTANTS["intercept"]) + sum(
        d.contribution for d in risk.drivers if d.feature != "exp"
    )
    assert risk.score == pytest.approx(round(10.0 * _sigmoid(logit), 1))


def test_top_drivers_sorted_by_magnitude() -> None:
    risk = score_change(_feat(la=300, ld=5, nf=2, nd=1, ns=1, entropy=0.5, exp=100))
    contribs = [abs(d.contribution) for d in risk.top_drivers]
    assert contribs == sorted(contribs, reverse=True)


def test_payload_includes_friendly_repo_relative_classification() -> None:
    features = _feat(la=50, ld=10, nf=5, nd=3, ns=2, entropy=2.0, exp=8)
    payload = change_risk_payload(
        ChangeRiskResult(
            features=features,
            risk=score_change(features),
            percentile=66.6,
            priority="moderate",
            baseline_sample_size=200,
            riskignore_excludes=(),
            request_excludes=(),
        )
    )

    assert payload["risk_percentile"] == 66.6
    assert payload["review_priority"] == "moderate"
    assert payload["classification"] == "Typical"
    assert payload["baseline_sample_size"] == 200
    assert payload["risk_authority"] == {
        "authoritative_for": "diff_size_rank",
        "primary_fields": ["risk_percentile"],
        "primary_basis": "population_relative_diff_size",
        "fallback_field": "fallback_band",
        "fallback_basis": "absolute_model_score_band",
        "score_role": "supporting_diff_shape_signal",
    }
    # The per-field dictionary never varies between calls, so it is opt-in.
    assert "risk_scales" not in payload
    expanded = change_risk_payload(
        ChangeRiskResult(
            features=features,
            risk=score_change(features),
            percentile=66.6,
            priority="moderate",
            baseline_sample_size=200,
            riskignore_excludes=(),
            request_excludes=(),
        ),
        scales=True,
    )
    scales = {scale["field"]: scale for scale in expanded["risk_scales"]}
    assert scales["score"]["unit"] == "normalized_points"
    assert scales["score"]["authoritative"] is False
    assert scales["risk_percentile"]["unit"] == "percentile_rank"
    assert scales["risk_percentile"]["authoritative"] is True
    assert scales["fallback_band"]["thresholds"] == {
        "moderate_score": 4.0,
        "high_score": 7.0,
    }
    # No uncalibrated scalar may be described as a probability, anywhere.
    assert "probability" not in str(expanded["risk_scales"]).lower()
    assert all(s["kind"] != "probability" for s in expanded["risk_scales"])
    assert all(s["unit"] != "probability" for s in expanded["risk_scales"])


# ---------------------------------------------------------------------------
# Diff-only feature builder (no git repo — the bot's PR-API path).
# ---------------------------------------------------------------------------


def test_features_from_file_changes_aggregates_diffusion() -> None:
    f = features_from_file_changes(
        [
            ("src/a.py", 10, 2),
            ("src/sub/b.py", 4, 0),
            ("pkg/c.py", 1, 1),
        ],
        exp=42,
        is_fix=True,
        ref="pr-123",
    )
    assert f.la == 15
    assert f.ld == 3
    assert f.nf == 3
    assert f.ns == 2  # src, pkg
    assert f.nd == 3  # src, src/sub, pkg
    assert f.exp == 42
    assert f.is_fix is True
    assert f.entropy > 0.0
    # The diff-only builder must score identically to the git path for the
    # same underlying counts.
    assert (
        score_change(f).score
        == score_change(
            ChangeFeatures(la=15, ld=3, nf=3, nd=3, ns=2, entropy=f.entropy, exp=42)
        ).score
    )


# ---------------------------------------------------------------------------
# Feature extraction from a real (tiny) git repo.
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo: Path, files: dict[str, str], message: str, author: str = "Tester") -> str:
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        _git(["add", rel], repo)
    _git(["-c", f"user.name={author}", "-c", "user.email=t@e.com", "commit", "-m", message], repo)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.name", "Seed"], repo)
    _git(["config", "user.email", "seed@e.com"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed", author="Seed")
    return repo


def test_repowise_state_alone_does_not_make_the_tree_dirty(git_repo: Path) -> None:
    """A fresh init leaves .repowise/ untracked; that is not a code change."""
    (git_repo / ".repowise").mkdir()
    (git_repo / ".repowise" / "wiki.db").write_text("x", encoding="utf-8")
    assert not working_tree_is_dirty(str(git_repo))
    (git_repo / "new.py").write_text("x = 1\n", encoding="utf-8")
    assert working_tree_is_dirty(str(git_repo))


def test_extract_commit_features_counts_diffusion(git_repo: Path) -> None:
    _commit(
        git_repo,
        {
            "src/a.py": "x = 1\ny = 2\nz = 3\n",
            "src/sub/b.py": "def f():\n    return 1\n",
            "pkg/c.py": "C = 1\n",
        },
        "fix: handle null input crash",
        author="Dev",
    )
    f = extract_commit_features(str(git_repo), "HEAD", extensions=(".py",))
    assert f.nf == 3
    assert f.la == 6  # 3 + 2 + 1 added lines
    assert f.ld == 0
    assert f.ns == 2  # src, pkg
    assert f.nd == 3  # src, src/sub, pkg
    assert f.is_fix is True
    assert f.entropy > 0.0  # churn spread across files


def test_extract_filters_by_extension(git_repo: Path) -> None:
    _commit(
        git_repo,
        {"keep.py": "a = 1\n", "skip.md": "doc\nmore\n"},
        "feat: add thing",
        author="Dev",
    )
    f = extract_commit_features(str(git_repo), "HEAD", extensions=(".py",))
    assert f.nf == 1
    assert f.la == 1
    assert f.is_fix is False


def test_extract_filters_by_gitignore_exclude_pattern(git_repo: Path) -> None:
    _commit(
        git_repo,
        {
            "src/app.py": "value = 1\n",
            "tests/test_app.py": "def test_value():\n    assert True\n",
            "web/app.spec.ts": "it('works', () => {})\n",
        },
        "feat: add application",
        author="Dev",
    )

    f = extract_commit_features(str(git_repo), "HEAD", exclude_patterns=("tests/", "*.spec.ts"))

    assert f.nf == 1
    assert f.la == 1
    assert f.nd == 1
    assert f.ns == 1


def test_extract_range_filters_by_gitignore_exclude_pattern(git_repo: Path) -> None:
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    _commit(git_repo, {"src/app.py": "value = 1\n"}, "feat: app", author="Dev")
    _commit(
        git_repo,
        {"tests/test_app.py": "def test_value():\n    assert True\n"},
        "test: app",
        author="Dev",
    )

    f = extract_range_features(str(git_repo), base, "HEAD", exclude_patterns=("tests/",))

    assert f.nf == 1
    assert f.la == 1


def test_author_experience_accrues(git_repo: Path) -> None:
    _commit(git_repo, {"f1.py": "a=1\n"}, "feat: one", author="Repeat")
    _commit(git_repo, {"f2.py": "b=2\n"}, "feat: two", author="Repeat")
    head = _commit(git_repo, {"f3.py": "c=3\n"}, "feat: three", author="Repeat")
    f = extract_commit_features(str(git_repo), head, extensions=(".py",))
    # Two prior "Repeat" commits exist before HEAD.
    assert f.exp == 2


def test_extract_range_features_aggregates(git_repo: Path) -> None:
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    _commit(git_repo, {"r/a.py": "a=1\n"}, "feat: a", author="Dev")
    _commit(git_repo, {"r/b.py": "b=2\nc=3\n"}, "fix: b crash", author="Dev")
    f = extract_range_features(str(git_repo), base, "HEAD", extensions=(".py",))
    assert f.nf == 2
    assert f.la == 3
    assert f.is_fix is True  # a fix commit is in the range
    assert f.ref == f"{base}..HEAD"


def test_score_change_on_real_commit(git_repo: Path) -> None:
    _commit(
        git_repo,
        {"big.py": "\n".join(f"line{i} = {i}" for i in range(120)) + "\n"},
        "feat: big drop",
        author="New",
    )
    f = extract_commit_features(str(git_repo), "HEAD", extensions=(".py",))
    risk = score_change(f)
    assert 0.0 <= risk.score <= 10.0
    assert risk.features is f
    assert not math.isnan(risk.score)


def test_extract_commit_features_bad_revspec_raises(git_repo: Path) -> None:
    # A bogus revspec must raise (git returns nonzero), not silently produce an
    # all-zero feature vector that scores as a low-risk change.
    with pytest.raises(subprocess.CalledProcessError):
        extract_commit_features(str(git_repo), "no-such-ref")


def test_score_live_change_bad_revspec_raises(git_repo: Path) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        score_live_change(str(git_repo), "no-such-ref", baseline=0)


def test_score_live_change_rejects_negative_baseline(git_repo: Path) -> None:
    with pytest.raises(ValueError, match="baseline"):
        score_live_change(str(git_repo), "HEAD", baseline=-1)


def test_score_live_change_three_dot_range_is_valid(git_repo: Path) -> None:
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    _commit(git_repo, {"r/a.py": "a=1\n"}, "feat: a", author="Dev")
    # Three-dot syntax (base...HEAD) resolves to valid refs and keeps its
    # merge-base meaning in the label.
    result = score_live_change(str(git_repo), f"{base}...HEAD", baseline=0)
    assert result.features.nf == 1
    assert result.features.ref == f"{base}...HEAD"


def test_score_live_change_below_min_baseline_yields_no_percentile(git_repo: Path) -> None:
    # A shallow repo (fewer than the 8-commit floor) can't produce a
    # representative percentile, so it degrades to no ranking, not a wrong one.
    _commit(git_repo, {"a.py": "a=1\n"}, "feat: a", author="Dev")
    result = score_live_change(str(git_repo), "HEAD", baseline=200)
    assert result.baseline_sample_size < 8
    assert result.percentile is None
    assert result.priority is None


def test_baseline_cache_hits_on_second_call_and_busts_on_new_commit(
    git_repo: Path, monkeypatch
) -> None:
    # The 200-commit baseline walk is the dominant cost of a default call and is
    # identical for the same repo state, so it is memoized on the resolved anchor
    # sha. Give the repo enough history to actually build a percentile.
    from repowise.core.analysis.change_risk import baseline

    for i in range(10):
        _commit(git_repo, {f"src/f{i}.py": f"x = {i}\n"}, f"feat: file {i}", author="Dev")

    baseline.clear_baseline_cache()
    calls = {"n": 0}
    real = baseline.baseline_samples

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(baseline, "baseline_samples", _counting)

    first = score_live_change(str(git_repo), "HEAD", baseline=200)
    second = score_live_change(str(git_repo), "HEAD", baseline=200)
    # Second identical call reuses the memo: no extra baseline walk.
    assert calls["n"] == 1
    assert first.percentile == second.percentile
    assert first.baseline_sample_size == second.baseline_sample_size

    # A new commit moves HEAD's sha, so the memo key changes and the walk reruns.
    _commit(git_repo, {"src/new.py": "y = 1\n"}, "feat: bust", author="Dev")
    score_live_change(str(git_repo), "HEAD", baseline=200)
    assert calls["n"] == 2

    baseline.clear_baseline_cache()


def _merged_change(repo: Path) -> str:
    """A merge commit with history before it and larger commits landing after it."""
    for i in range(10):
        _commit(repo, {f"src/f{i}.py": f"x = {i}\n"}, f"feat: file {i}", author="Dev")
    _git(["checkout", "-q", "-b", "feature"], repo)
    _commit(repo, {"src/pr.py": "".join(f"p{i} = {i}\n" for i in range(6))}, "feat: pr", "Dev")
    _git(["checkout", "-q", "-"], repo)
    _commit(repo, {"src/main.py": "m = 1\n"}, "feat: main moves", author="Dev")
    merge_cmd = ["merge", "-q", "--no-ff", "feature", "-m", "Merge pr"]
    _git(["-c", "user.name=Dev", "-c", "user.email=t@e.com", *merge_cmd], repo)
    merge = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    # Larger commits after the merge: a sample read at HEAD would rank it lower.
    for i in range(10):
        body = "".join(f"y{j} = {j}\n" for j in range(40))
        _commit(repo, {f"src/later{i}.py": body}, f"feat: later {i}", author="Dev")
    return merge


def test_a_change_ranks_the_same_however_it_is_spelled(git_repo: Path) -> None:
    # The commit, its first-parent range and its checklist rank against one
    # sample: the commits before the change, wherever HEAD has moved since.
    import asyncio

    from repowise.core.analysis.change_risk import baseline
    from repowise.core.changes.collect import evidence_for_revspec
    from repowise.core.changes.evidence import item_line

    merge = _merged_change(git_repo)
    parent = subprocess.run(
        ["git", "rev-parse", f"{merge}^1"],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    baseline.clear_baseline_cache()

    commit = score_live_change(str(git_repo), merge, baseline=200)
    ranged = score_live_change(str(git_repo), f"{merge}^1..{merge}", baseline=200)
    checklist = asyncio.run(evidence_for_revspec(None, "", str(git_repo), merge))
    row = next(i for i in checklist.items if i.kind == "risk_score").to_dict()
    line = item_line(row)

    assert commit.percentile is not None
    assert commit.percentile == ranged.percentile
    assert commit.baseline_as_of == ranged.baseline_as_of == parent
    assert commit.baseline_sample_size == ranged.baseline_sample_size
    assert line["key"] == "risk_score_before"
    assert line["params"] == {
        "percentile": round(commit.percentile, 1),
        "sample": commit.baseline_sample_size,
        "as_of": parent[:10],
    }
    baseline.clear_baseline_cache()


def test_baseline_cache_shared_by_spellings_of_one_change(git_repo: Path, monkeypatch) -> None:
    # The memo is keyed on the anchor, so a commit and its first-parent range
    # share one walk.
    from repowise.core.analysis.change_risk import baseline

    for i in range(10):
        _commit(git_repo, {f"src/f{i}.py": f"x = {i}\n"}, f"feat: file {i}", author="Dev")
    head = _commit(git_repo, {"src/b.py": "b = 2\n"}, "feat: b", author="Dev")

    baseline.clear_baseline_cache()
    calls = {"n": 0}
    real = baseline.baseline_samples

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(baseline, "baseline_samples", _counting)

    score_live_change(str(git_repo), head, baseline=200)
    score_live_change(str(git_repo), f"{head}^..{head}", baseline=200)
    score_live_change(str(git_repo), baseline=200)  # clean tree -> HEAD

    assert calls["n"] == 1
    baseline.clear_baseline_cache()


def test_commit_does_not_rank_against_itself(git_repo: Path) -> None:
    from repowise.core.analysis.change_risk import baseline

    for i in range(10):
        _commit(git_repo, {f"src/f{i}.py": f"x = {i}\n"}, f"feat: file {i}", author="Dev")

    baseline.clear_baseline_cache()
    samples = baseline.baseline_samples(str(git_repo), "HEAD", 200, ())
    result = score_live_change(str(git_repo), "HEAD", baseline=200)

    # The sample runs back from the parent, so it is every commit but the target.
    assert result.baseline_sample_size == len(samples) - 1
    baseline.clear_baseline_cache()


def test_baseline_cache_isolated_by_filters(git_repo: Path, monkeypatch) -> None:
    # Different filters produce different samples, so they must not share a memo
    # entry even at the same anchor sha.
    from repowise.core.analysis.change_risk import baseline

    for i in range(10):
        _commit(
            git_repo,
            {f"src/f{i}.py": f"x = {i}\n", f"docs/d{i}.md": f"# {i}\n"},
            f"feat: file {i}",
            author="Dev",
        )

    baseline.clear_baseline_cache()
    calls = {"n": 0}
    real = baseline.baseline_samples

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(baseline, "baseline_samples", _counting)

    score_live_change(str(git_repo), "HEAD", baseline=200)
    score_live_change(str(git_repo), "HEAD", baseline=200, extensions=("py",))
    # Distinct extension filters key separately, so both walked.
    assert calls["n"] == 2

    baseline.clear_baseline_cache()


# ---------------------------------------------------------------------------
# The unit actually scored: working tree, merge commits, unknown authors.
# ---------------------------------------------------------------------------


def test_default_revspec_scores_the_uncommitted_change(git_repo: Path) -> None:
    """No revspec + a dirty tree means the code the caller just wrote."""
    _commit(git_repo, {"src/a.py": "x = 1\n"}, "feat: a", author="Dev")
    (git_repo / "src" / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")  # unstaged
    (git_repo / "src" / "staged.py").write_text("s = 1\n", encoding="utf-8")
    _git(["add", "src/staged.py"], git_repo)
    (git_repo / "src" / "new.py").write_text("n = 1\nm = 2\n", encoding="utf-8")  # untracked

    result = score_live_change(str(git_repo), baseline=0)

    assert result.working_tree is True
    assert result.features.ref == "working tree"
    assert result.features.nf == 3  # unstaged edit, staged add, untracked add
    assert result.features.la == 4  # 1 + 1 + 2
    assert change_risk_payload(result)["working_tree"] is True


def test_dirt_the_filters_drop_falls_back_to_head(git_repo: Path) -> None:
    """A tree dirty only in excluded paths is not a change we can score."""
    _commit(git_repo, {"src/a.py": "x = 1\n"}, "feat: a", author="Dev")
    (git_repo / "notes.md").write_text("scratch\n", encoding="utf-8")

    result = score_live_change(str(git_repo), extensions=(".py",), baseline=0)

    assert result.working_tree is False
    assert result.features.ref == "HEAD"
    assert result.features.nf == 1  # the committed src/a.py, not an empty change


def _block_format() -> str:
    from repowise.core.generation.editor_files.base import BaseEditorFileGenerator as Gen

    start = Gen.MARKER_START_FMT.format(tag="REPOWISE")
    return start + "\n{body}\n" + Gen.MARKER_END_FMT.format(tag="REPOWISE") + "\n"


_BLOCK = _block_format()


def test_repowise_output_alone_falls_back_to_head(git_repo: Path) -> None:
    """The index, a written .mcp.json and a refreshed managed block are not the user's change."""
    _commit(
        git_repo, {".claude/CLAUDE.md": "# Mine\n\n" + _BLOCK.format(body="v1")}, "docs", "Dev"
    )
    (git_repo / ".claude" / "CLAUDE.md").write_text(
        "# Mine\n\n" + _BLOCK.format(body="v2\nmore"), encoding="utf-8"
    )
    (git_repo / ".repowise").mkdir()
    (git_repo / ".repowise" / "config.yaml").write_text("provider: x\n", encoding="utf-8")
    (git_repo / ".mcp.json").write_text(
        '{"mcpServers": {"repowise": {"command": "repowise"}}}', encoding="utf-8"
    )
    (git_repo / "AGENTS.md").write_text(_BLOCK.format(body="hi"), encoding="utf-8")

    result = score_live_change(str(git_repo), baseline=0)

    assert result.working_tree is False
    assert result.features.ref == "HEAD"
    assert ".repowise/" in change_risk_payload(result)["exclude_patterns"]


def test_a_users_edit_to_a_managed_file_still_counts(git_repo: Path) -> None:
    block = _BLOCK.format(body="v1")
    _commit(git_repo, {".claude/CLAUDE.md": "# Mine\n\n" + block}, "docs", "Dev")
    (git_repo / ".claude" / "CLAUDE.md").write_text("# Mine, edited\n\n" + block, encoding="utf-8")
    (git_repo / ".mcp.json").write_text(
        '{"mcpServers": {"repowise": {}, "mine": {"command": "x"}}}', encoding="utf-8"
    )

    result = score_live_change(str(git_repo), baseline=0)

    assert result.working_tree is True
    assert {path for path, _ in result.features.file_churn} == {".claude/CLAUDE.md", ".mcp.json"}


def _working_tree_file_sets(repo: Path, result) -> tuple[set[str], set[str]]:
    """The files the score and the changed lines read; the health delta must count as many."""
    from repowise.core.analysis.change_health.service import (
        ChangeHealthDeltaService,
        DeltaRequest,
    )
    from repowise.core.analysis.changed_lines import changed_lines

    scored = {path for path, _ in result.features.file_churn}
    changed, _ = changed_lines(str(repo), working_tree=True)
    delta = ChangeHealthDeltaService(repo_path=str(repo)).compare(
        DeltaRequest(str(repo), None, (), result.riskignore_excludes)
    )
    assert delta.scope.changed == result.features.nf
    return scored, set(changed)


def test_untracked_files_are_one_file_set_for_score_lines_and_delta(git_repo: Path) -> None:
    """An untracked-only tree is a real change everywhere, never "high" beside "no files"."""
    (git_repo / "src").mkdir()
    (git_repo / "src" / "new.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (git_repo / "notes.md").write_text("n\n", encoding="utf-8")

    result = score_live_change(str(git_repo), baseline=0)

    assert result.working_tree is True
    assert _working_tree_file_sets(git_repo, result) == ({"src/new.py", "notes.md"},) * 2
    assert result.features.la == 3


def test_a_tracked_edit_and_an_untracked_file_both_count(git_repo: Path) -> None:
    _commit(git_repo, {"src/a.py": "x = 1\n"}, "feat: a", author="Dev")
    (git_repo / "src" / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (git_repo / "src" / "new.py").write_text("n = 1\n", encoding="utf-8")
    (git_repo / ".repowise").mkdir()
    (git_repo / ".repowise" / "wiki.db").write_bytes(b"\x00" * 64)

    result = score_live_change(str(git_repo), baseline=0)

    assert result.features.nf == 2
    assert _working_tree_file_sets(git_repo, result) == ({"src/a.py", "src/new.py"},) * 2


def test_owned_excludes_name_repowise_output_not_the_users_filters(git_repo: Path) -> None:
    """Test selection drops only repowise's output; .riskignore and request excludes stay."""
    _commit(git_repo, {"src/a.py": "x = 1\n", ".riskignore": "vendor/\n"}, "feat: a", "Dev")
    (git_repo / "src" / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (git_repo / "AGENTS.md").write_text(_BLOCK.format(body="hi"), encoding="utf-8")
    (git_repo / ".repowise").mkdir()
    (git_repo / ".repowise" / "config.yaml").write_text("provider: x\n", encoding="utf-8")

    result = score_live_change(str(git_repo), baseline=0, exclude_patterns=("docs/",))

    assert result.working_tree is True
    assert result.owned_excludes == (".repowise/", "/AGENTS.md")
    assert {"vendor/", "docs/"} <= set(result.riskignore_excludes + result.request_excludes)


def test_explicit_head_scores_the_commit_even_when_dirty(git_repo: Path) -> None:
    """An explicit revspec always means committed refs."""
    _commit(git_repo, {"src/a.py": "x = 1\n"}, "feat: a", author="Dev")
    (git_repo / "src" / "dirty.py").write_text("d = 1\n", encoding="utf-8")

    result = score_live_change(str(git_repo), "HEAD", baseline=0)

    assert result.working_tree is False
    assert result.features.nf == 1
    assert result.features.la == 1
    assert change_risk_payload(result)["working_tree"] is False


def test_default_revspec_falls_back_to_head_when_clean(git_repo: Path) -> None:
    head = _commit(git_repo, {"src/a.py": "x = 1\n"}, "feat: a", author="Dev")

    result = score_live_change(str(git_repo), baseline=0)

    assert result.working_tree is False
    assert result.features.ref == "HEAD"
    assert result.features.subject == "feat: a"
    assert head  # the tree is clean, so the commit is the subject


def test_merge_commit_scores_its_first_parent_diff(git_repo: Path) -> None:
    """A merge is scored for what it brought onto the first parent.

    Combined-diff semantics drop every file that matches a parent, which would
    score a whole merged PR as an empty change.
    """
    _git(["checkout", "-q", "-b", "side"], git_repo)
    _commit(git_repo, {"side.py": "s = 1\ns2 = 2\n"}, "feat: side", author="Dev")
    _git(["checkout", "-q", "-"], git_repo)
    _commit(git_repo, {"main.py": "m = 1\n"}, "feat: main", author="Dev")
    _git(
        [
            "-c",
            "user.name=Dev",
            "-c",
            "user.email=t@e.com",
            "merge",
            "-q",
            "--no-ff",
            "side",
            "-m",
            "merge: side",
        ],
        git_repo,
    )

    f = extract_commit_features(str(git_repo), "HEAD", extensions=(".py",))

    # side.py is exactly what the merge added to the first parent; main.py was
    # already there. The reverse holds for the second parent, so a combined
    # diff would report neither.
    assert f.nf == 1
    assert f.la == 2
    assert f.exp is not None  # ``sha^`` is the first parent, so the walk resolves


def test_failed_author_lookup_is_unknown_not_zero(git_repo: Path, monkeypatch) -> None:
    """A lookup that fails must not read as a first-ever commit.

    ``exp=0`` is a real value the model pushes risk up for; ``None`` is the
    neutral path it already has for a caller with no history to read.
    """
    from repowise.core.analysis.change_risk import features as feature_mod

    _commit(git_repo, {"a.py": "x = 1\n"}, "feat: a", author="Dev")
    real_git = feature_mod._git

    def _fail_rev_list(args, cwd, *, check=True):
        if args[:1] == ["rev-list"]:
            return "fatal: bad revision\n"  # what check=False yields on error
        return real_git(args, cwd, check=check)

    monkeypatch.setattr(feature_mod, "_git", _fail_rev_list)
    f = extract_commit_features(str(git_repo), "HEAD")

    assert f.exp is None
    exp_driver = next(d for d in score_change(f).drivers if d.feature == "exp")
    assert exp_driver.contribution == 0.0


def test_collinear_diffusion_features_are_not_reported_as_drivers() -> None:
    """nf/nd/ns still enter the logit; they just stop explaining it."""
    risk = score_change(_feat(la=300, ld=20, nf=25, nd=10, ns=5, entropy=3.0, exp=50))

    reported = {d.feature for d in risk.top_drivers}
    assert reported.isdisjoint({"nf", "nd", "ns"})
    assert {"la", "ld", "entropy", "exp"} <= reported
    # The full attribution is untouched, so the logit still reconstructs.
    logit = float(_CONSTANTS["intercept"]) + sum(d.contribution for d in risk.drivers)
    assert risk.score == pytest.approx(round(10.0 * _sigmoid(logit), 1))


def test_payload_offers_the_absolute_band_only_as_a_fallback(git_repo: Path) -> None:
    for i in range(12):
        _commit(git_repo, {f"f{i}.py": f"x = {i}\n"}, f"feat: {i}", author="Dev")

    ranked = change_risk_payload(score_live_change(str(git_repo), "HEAD", baseline=200))
    unranked = change_risk_payload(score_live_change(str(git_repo), "HEAD", baseline=0))

    assert ranked["review_priority"] is not None
    assert ranked["fallback_band"] is None
    assert unranked["review_priority"] is None
    assert unranked["fallback_band"] in {"low", "moderate", "high"}
    assert unranked["score_unit"] == "per-commit"
    assert "probability" not in ranked and "level" not in ranked


@pytest.mark.parametrize(
    "features",
    [
        ChangeFeatures(la=la, ld=ld, nf=nf, nd=nd, ns=ns, entropy=ent, exp=exp)
        for la in (0, 3, 40, 900)
        for ld in (0, 25, 400)
        for nf, nd, ns, ent in ((1, 1, 1, 0.0), (6, 3, 2, 2.1), (80, 20, 6, 5.5))
        for exp in (None, 0, 12, 4000)
    ],
)
def test_top_driver_is_the_argmax_of_the_shown_contributions(features) -> None:
    """The list's "top driver" is the biggest score-raising row the breakdown shows."""
    risk = score_change(features)
    shown = [d for d in risk.top_drivers if d.value is not None]
    raising = [d for d in shown if d.contribution > 0]
    if not raising:
        assert risk.top_driver is None
        return
    assert risk.top_driver is not None
    assert risk.top_driver.contribution == max(d.contribution for d in shown)
    assert risk.top_driver in shown
