"""prior_defect counts fixes by the commit's conventional type before its words.

Subjects are taken from this repository's own history.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.git_indexer._constants import is_fix_commit
from repowise.core.ingestion.git_indexer.fix_rule import is_typed_fix_commit
from repowise.core.ingestion.git_indexer.prior_defects import compute_prior_defects

NOT_FIXES = [
    "refactor(health): split the Fix-first builder into unit modules (#4071)",
    "refactor(context): resolve get_context targets through an ordered resolver list (#3988)",
    "feat(health): order Fix first by the function's own change history (#3565)",
    "test(web): align fix-first-section scope and exclusions assertions (#3339)",
    "perf(walker): resolve names once per file",
    "docs: explain the bug in the FAQ",
    "chore: fix lint",
    "ci: patch the release workflow",
    "build(deps): bump vite",
    "style: fix import order",
    "feat: add retry, fixes #12",
    "handle crash on startup",
]
FIXES = [
    "fix(deps): patch fsspec, Mako, multidict, source-map-js, postcss-selector-parser and KaTeX advisories (#3887)",
    "fix(update): write the repair marker after a docs-mode update (#4026)",
    "fix(ui): portal the docs Export menu so the header doesn't clip it (#3778)",
    "fix: null check in parser",
    "hotfix: restore login",
    "bugfix: off-by-one in pager",
    "Fix crash when the index is empty",
    "Resolve race in scheduler",
    "revert(api): fix the retry loop",
    "Fixes: crash on an empty index",
    "Bug: pager skips the last row",
    "Resolves: race in scheduler",
    "fix!: drop the broken retry",
    'Revert "fix: x"',
]


@pytest.mark.parametrize("subject", NOT_FIXES)
def test_a_non_fix_type_or_wording_is_not_a_fix(subject: str) -> None:
    assert not is_typed_fix_commit(subject)


@pytest.mark.parametrize("subject", FIXES)
def test_a_fix_type_or_untyped_fix_wording_is_a_fix(subject: str) -> None:
    assert is_typed_fix_commit(subject)


def test_the_benchmark_rule_is_unchanged() -> None:
    assert is_fix_commit(NOT_FIXES[0])
    assert not is_fix_commit(FIXES[0])


def _repo(tmp_path):
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    repo.config_writer().set_value("user", "name", "T").release()
    return repo


def test_prior_defect_counts_by_the_typed_rule(tmp_path) -> None:
    import git as gitpython

    repo = _repo(tmp_path)
    subjects = ["feat: add a", NOT_FIXES[0], NOT_FIXES[1], FIXES[0], FIXES[1]]
    for i, subject in enumerate(subjects):
        (tmp_path / "a.py").write_text(f"def f():\n    return {i}\n", encoding="utf-8")
        repo.index.add(["a.py"])
        repo.index.commit(subject)

    counts = compute_prior_defects(gitpython.Repo(tmp_path), {"a.py"}, as_of_ts=None)

    assert counts.counts == {"a.py": 2}
    assert counts.raw_counts == {"a.py": 2}


def test_change_risk_flags_a_commit_by_the_typed_rule(tmp_path) -> None:
    from repowise.core.analysis.change_risk.features import (
        extract_commit_features,
        extract_range_features,
    )

    repo = _repo(tmp_path)
    shas = []
    for i, subject in enumerate(["feat: add a", NOT_FIXES[1], FIXES[0]]):
        (tmp_path / "a.py").write_text(f"x = {i}\n", encoding="utf-8")
        repo.index.add(["a.py"])
        shas.append(repo.index.commit(subject).hexsha)

    assert not extract_commit_features(str(tmp_path), shas[1]).is_fix
    assert extract_commit_features(str(tmp_path), shas[2]).is_fix
    assert not extract_range_features(str(tmp_path), shas[0], shas[1]).is_fix
