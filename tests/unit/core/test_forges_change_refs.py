"""Change-request numbers read out of commit messages, per forge."""

from __future__ import annotations

import re

import pytest

from repowise.core.forges import ForgeKind, change_number, change_refs, get_forge
from repowise.core.forges.changes import _merge_forms, lead_of

GH, GL, AZ, BB, GEN = (
    ForgeKind.GITHUB,
    ForgeKind.GITLAB,
    ForgeKind.AZURE,
    ForgeKind.BITBUCKET,
    ForgeKind.GENERIC,
)


@pytest.mark.parametrize(
    ("kind", "subject", "body", "expected"),
    [
        # GitHub: squash suffix, merge commit, PR links. A bare #N is an issue.
        (GH, "feat: add x (#123)", "", [123]),
        (GH, "fix: y (gh-45)", "", [45]),
        (GH, "Merge pull request #456 from org/branch", "Some title", [456]),
        (GH, "fix: z", "See https://github.com/o/r/pull/77 for context", [77]),
        (GH, "fix: closes #9", "Fixes #10", []),
        (GH, "feat: x (#12) (#12)", "https://github.com/o/r/pull/12", [12]),
        (GH, "Revert \"feat: a (#5)\" (#6)", "", [5, 6]),
        (GH, "chore: wow!12", "", []),
        # GitLab: merge-commit trailer, !N shorthand, MR links.
        (GL, "Merge branch 'feat' into 'main'", "Title\n\nSee merge request group/sub/proj!42", [42]),
        (GL, "feat: thing (!17)", "", [17]),
        (GL, "fix: a", "Related to !3 and !4", [3, 4]),
        (GL, "fix: a", "https://gitlab.com/g/p/-/merge_requests/88", [88]),
        (GL, "chore: wow!!1 and word!2", "", []),
        (GL, "fix: issue #12", "", []),
        # Azure: completion message and PR links.
        (AZ, "Merged PR 1234: Add the thing", "", [1234]),
        (AZ, "Merge pull request 77 from feat into main", "", [77]),
        (AZ, "fix: a", "https://dev.azure.com/o/p/_git/r/pullrequest/55", [55]),
        (AZ, "fix: a (#12)", "", []),
        # Bitbucket: Cloud and Data Center merge messages, PR links.
        (BB, "Merged in feature/x (pull request #12)", "Approved-by: someone", [12]),
        (BB, "Merge pull request #34 in PROJ/repo from feat to master", "", [34]),
        (BB, "fix: a", "https://bitbucket.org/ws/repo/pull-requests/9", [9]),
        (BB, "fix: a (#12)", "", []),
        # Generic reads the GitHub way.
        (GEN, "feat: add x (#123)", "", [123]),
        (GEN, "Merged PR 12: a", "", []),
        (GH, "", "", []),
    ],
)
def test_parse_change_refs(kind: ForgeKind, subject: str, body: str, expected: list[int]) -> None:
    assert get_forge(kind).parse_change_refs(subject, body) == expected


@pytest.mark.parametrize("kind", list(ForgeKind))
def test_a_huge_number_is_not_a_change_ref(kind: ForgeKind) -> None:
    """``int()`` refuses 4300+ digits; a run that long names no change."""
    digits = "9" * 50_000
    subject = f"Merged PR {digits}: x (#{digits}) Merge pull request #{digits} from a !{digits}"
    body = f"/pull/{digits} /-/merge_requests/{digits} /pullrequest/{digits} /pull-requests/{digits}"
    assert get_forge(kind).parse_change_refs(subject, body) == []
    assert change_number(subject, f"See merge request g/p!{digits}", kind) is None
    assert change_refs(subject, body, kind) == []


@pytest.mark.parametrize(
    ("forge", "subject", "body", "expected"),
    [
        # GitHub: the squash suffix closing the subject, or a merge subject.
        (GH, "feat: add x (#123)", "", 123),
        (GH, "feat: add x (gh-45)  ", "", 45),
        (GH, "Merge pull request #456 from org/branch", "", 456),
        # Only the suffix is the PR; an issue named before it is not.
        (GH, "fix(x): y (#881) (#911)", "", 911),
        (GH, "Fix #2379: classify dotfiles (#2381)", "", 2381),
        # A bare #N is an issue as often as a PR, and a link is a mention.
        (GH, "fix: closes #12", "", None),
        (GH, "feat: add JVM PKCS#12 helper", "", None),
        (GH, "fix: a", "Follow-up to https://github.com/o/r/pull/77", None),
        (GH, "fix: a", "* feat: squashed (#5)", None),
        # A direct revert merged no PR; a revert that went through one did.
        (GH, 'Revert "feat: a (#5)"', "", None),
        (GH, 'Revert "feat: a (#5)" (#6)', "", 6),
        # GitLab: the merge trailer, in either form, on any forge.
        (GL, "Merge branch 'f' into 'main'", "Title\n\nSee merge request g/sub/p!42", 42),
        (
            GH,
            "Merge branch 'f' into 'main'",
            "See merge request https://gitlab.com/g/p/-/merge_requests/4009\n",
            4009,
        ),
        # The body's own trailer is its last; on GitLab it outranks a suffix.
        (GL, "Add x", "See merge request g/p!5 for context\nSee merge request g/p!9", 9),
        (GL, "Backport (#7)", "(cherry picked from commit abc)\nSee merge request g/p!3", 3),
        (GH, "Backport (#7)", "See merge request g/p!3", 7),
        # A suffix followed by a full stop or a CI tag still closes the subject.
        (GH, "fix foo (#12) [skip ci]", "", 12),
        (GH, "fix foo (#12).", "", 12),
        # GitLab's (!N) suffix counts on GitLab only; a bare !N is a mention.
        (GL, "feat: thing (!17)", "", 17),
        (GH, "feat: thing (!17)", "", None),
        (GL, "fix(orbit): address review nits from !3269", "", None),
        (GL, "fix: issue #12", "", None),
        # Azure DevOps and Bitbucket merge messages, read on any forge.
        (AZ, "Merged PR 1234: Add the thing", "", 1234),
        (GH, "Merged PR 1234: Add the thing", "", 1234),
        (AZ, "Merge pull request 77 from feat into main", "", 77),
        (BB, "Merged in feature/x (pull request #12)", "Approved-by: a", 12),
        (GL, "Merge pull request #34 in PROJ/repo from feat to master", "", 34),
        (BB, "fix: see (pull request #12) for why", "", None),
        # Azure's AB#N links a work item, never a PR.
        (AZ, "fix: a AB#123", "", None),
        (GEN, "", "", None),
    ],
)
def test_change_number(forge: ForgeKind, subject: str, body: str, expected: int | None) -> None:
    assert change_number(subject, body, forge) == expected


@pytest.mark.parametrize(
    ("forge", "subject", "body", "expected"),
    [
        (GH, "revert: x", "Reverts https://github.com/o/r/pull/7", [7]),
        (GL, "revert: x", "Reverts !7", [7]),
        # Off GitLab, !7 is punctuation; the explicit forms still count.
        (GH, "revert: x", "Reverts !7", []),
        (GH, "revert: x", "See https://gitlab.com/g/p/-/merge_requests/8", [8]),
        (GH, "revert: x (#3)", "https://dev.azure.com/o/p/_git/r/pullrequest/9", [3, 9]),
        (AZ, "fix: a AB#123", "", []),
    ],
)
def test_change_refs_reads_every_forge_with_the_repos_first(
    forge: ForgeKind, subject: str, body: str, expected: list[int]
) -> None:
    assert change_refs(subject, body, forge) == expected


@pytest.mark.parametrize("kind", list(ForgeKind))
def test_the_string_gates_never_hide_a_match(kind: ForgeKind) -> None:
    """Each merge form's lead is text it cannot match without."""
    subject_forms, body_forms, _ = _merge_forms(kind)
    samples = [
        "feat: x (#1)",
        "feat: x (GH-2) ",
        "feat: x (!3)",
        "Merged PR 4: x",
        "Merge pull request 5 from a into b",
        "Merge pull request #6 in P/r from a",
        "Merged in b (pull request #7)",
        "x\nSee merge request g/p!8",
    ]
    hidden = [
        (pattern.pattern, text)
        for forms in (subject_forms, body_forms)
        for _, pattern in (*forms.anchored, *forms.floating)
        for text in samples
        if pattern.search(text) and forms.first(text) is None
    ]
    assert hidden == []


@pytest.mark.parametrize(
    ("pattern", "lead"),
    [
        (r"^Merged PR (\d+):", "Merged PR "),
        (r"\(!(\d+)\)\s*$", "(!"),
        (r"See merge request \S*?!(\d+)", "See merge request "),
        (r"ab?c(\d)", "a"),
        (r"\(x*(\d)", "("),
    ],
)
def test_lead_of(pattern: str, lead: str) -> None:
    assert lead_of(re.compile(pattern)) == lead


@pytest.mark.parametrize("pattern", [r"(\d+)", r"(?i)abc(\d)", r"abc(\d)(\d)"])
def test_a_merge_form_without_a_lead_is_refused(pattern: str) -> None:
    with pytest.raises(ValueError):
        lead_of(re.compile(pattern))
