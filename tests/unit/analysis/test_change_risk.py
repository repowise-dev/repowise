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
    assert 0.0 <= risk.probability <= 1.0
    assert 0.0 <= risk.score <= 10.0
    assert risk.level in {"low", "moderate", "high"}


def test_logit_is_exact_sum_of_driver_contributions() -> None:
    """The reported per-driver contributions must reconstruct the probability
    exactly — the attribution is the model, not a post-hoc approximation."""
    f = _feat(la=50, ld=10, nf=5, nd=3, ns=2, entropy=2.0, exp=8)
    risk = score_change(f)
    logit = float(_CONSTANTS["intercept"]) + sum(d.contribution for d in risk.drivers)
    assert risk.probability == pytest.approx(_sigmoid(logit))
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
    assert risk.probability == pytest.approx(_sigmoid(logit))


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
    assert not math.isnan(risk.probability)


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
    # Three-dot syntax (base...HEAD) must degrade to a valid anchor rather than
    # leaving a leading dot that git rejects as a ref.
    result = score_live_change(str(git_repo), f"{base}...HEAD", baseline=0)
    assert result.features.nf == 1
    assert result.features.ref == f"{base}..HEAD"


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
    real = baseline.baseline_scores

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(baseline, "baseline_scores", _counting)

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
    real = baseline.baseline_scores

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(baseline, "baseline_scores", _counting)

    score_live_change(str(git_repo), "HEAD", baseline=200)
    score_live_change(str(git_repo), "HEAD", baseline=200, extensions=("py",))
    # Distinct extension filters key separately, so both walked.
    assert calls["n"] == 2

    baseline.clear_baseline_cache()
