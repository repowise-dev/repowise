"""Author identity across forges: noreply folding and automation accounts."""

from __future__ import annotations

import pytest

from repowise.core.forges import (
    ForgeKind,
    all_forges,
    canonical_email,
    is_bot,
    noreply_login,
    normalize_identity,
)

GH, GL = ForgeKind.GITHUB, ForgeKind.GITLAB


@pytest.mark.parametrize(
    ("email", "name", "canonical", "bot"),
    [
        # GitHub: both noreply forms fold to the id-less one.
        ("123+Octo@users.noreply.github.com", "Octo Cat", "octo@users.noreply.github.com", False),
        ("octo@users.noreply.github.com", "Octo", "octo@users.noreply.github.com", False),
        ("123-octo@users.noreply.github.com", "o", "123-octo@users.noreply.github.com", False),
        (" Dev@Example.COM ", "Dev", "dev@example.com", False),
        ("49699333+dependabot[bot]@users.noreply.github.com", "dependabot[bot]",
         "dependabot[bot]@users.noreply.github.com", True),
        ("actions@github.com", "GitHub Actions", "actions@github.com", True),
        ("noreply@github.com", "GitHub", "noreply@github.com", True),
        ("x@users.noreply.example.com", "github-actions", "x@users.noreply.example.com", True),
        ("1+bot@bots.noreply.github.com", "Some App", "1+bot@bots.noreply.github.com", True),
        # GitLab: the id is always there, so the address is kept as is.
        ("1234-jdoe@users.noreply.gitlab.com", "Jane Doe",
         "1234-jdoe@users.noreply.gitlab.com", False),
        ("77-JDoe@users.noreply.git.corp.com", "Jane", "77-jdoe@users.noreply.git.corp.com", False),
        ("project_42_bot_3f2a@noreply.gitlab.com", "deploy token",
         "project_42_bot_3f2a@noreply.gitlab.com", True),
        ("group_7_bot@noreply.git.corp.com", "group_7_bot", "group_7_bot@noreply.git.corp.com", True),
        ("bot@corp.com", "GitLab Bot", "bot@corp.com", True),
        ("ghost@corp.com", "Ghost User", "ghost@corp.com", True),
        ("ghost@corp.com", "ghost", "ghost@corp.com", False),
        ("project_1_botany@ex.com", "Pat", "project_1_botany@ex.com", False),
        # Azure DevOps
        ("build@org.com", "Project Collection Build Service (contoso)", "build@org.com", True),
        ("x@y.com", "Fabrikam Build Service (contoso)", "x@y.com", True),
        ("", "azure-pipelines", "", True),
        ("", "azure-pipelines[bot]", "", True),
        ("x@y.com", "Build Service", "x@y.com", False),
        # Bitbucket
        ("commits-noreply@bitbucket.org", "bitbucket-pipelines",
         "commits-noreply@bitbucket.org", True),
        ("commits-noreply@bitbucket.org", "Jane Doe", "commits-noreply@bitbucket.org", True),
        # People stay people.
        ("dev@corp.com", "Dev", "dev@corp.com", False),
        ("n@corp.com", "Netlify Johnson", "n@corp.com", False),
        ("r@corp.com", "Abbot", "r@corp.com", False),
        ("", "", "", False),
    ],
)
def test_normalize_identity(email: str, name: str, canonical: str, bot: bool) -> None:
    assert normalize_identity(email, name) == (canonical, bot)
    # An address keeps its minting forge's shape, so every forge reads it alike.
    for forge in all_forges():
        assert forge.normalize_identity(email, name) == (canonical, bot)


@pytest.mark.parametrize(
    "name",
    [
        "dependabot", "renovate", "renovatebot", "greenkeeper", "snyk", "snyk-bot", "imgbot",
        "github-actions", "GitHub Actions", "semantic-release", "allcontributors", "codecov",
        "mergify", "pre-commit-ci", "netlify", "vercel", "bot", "release-bot", "ci_bot",
        "renovate[bot]",
    ],
)
def test_forge_neutral_bot_names(name: str) -> None:
    assert is_bot(name, "")


@pytest.mark.parametrize(
    ("email", "login"),
    [
        ("123+Octo@users.noreply.github.com", (GH, "octo")),
        ("octo@users.noreply.github.com", (GH, "octo")),
        ("1234-jdoe@users.noreply.gitlab.com", (GL, "jdoe")),
        ("9-a-b@users.noreply.git.corp.com", (GL, "a-b")),
        ("jdoe@users.noreply.gitlab.com", None),  # GitLab always writes the id
        ("project_1_bot_ab@noreply.gitlab.com", None),
        ("noreply@github.com", None),
        ("dev@example.com", None),
        ("", None),
    ],
)
def test_noreply_login(email: str, login: tuple[ForgeKind, str] | None) -> None:
    assert noreply_login(email) == login


def test_canonical_email_handles_none() -> None:
    assert canonical_email(None) == ""
