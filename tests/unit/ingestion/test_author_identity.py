"""Tests for GitHub noreply author-identity canonicalization.

Covers the identity chokepoints that split one person into several contributor
buckets: the shared ``canonicalize_author_email`` helper, the ``owner_key``
that keys the contributor directory, the commit-experience tally key, and the
per-file author-email selection in ``index_file``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import git
import pytest

from repowise.core.ingestion.git_indexer import (
    build_identity_resolver,
    canonicalize_author_email,
)
from repowise.core.ingestion.git_indexer.identity import author_identity_key
from repowise.core.ingestion.git_indexer.records import capture_repo_totals, count_people
from repowise.core.stats_highlights import build_people
from repowise.server.services.owner_profile import owner_key


class TestCanonicalizeAuthorEmail:
    def test_numeric_prefixed_noreply_folds_to_login(self) -> None:
        assert (
            canonicalize_author_email("12345+jane@users.noreply.github.com")
            == "jane@users.noreply.github.com"
        )

    def test_all_noreply_variants_of_one_login_share_a_key(self) -> None:
        # Old (no numeric id) and new (numeric id) forms, plus a re-issued id,
        # all collapse to the same canonical identity.
        variants = [
            "jane@users.noreply.github.com",
            "1+jane@users.noreply.github.com",
            "999999+jane@users.noreply.github.com",
            "12345+Jane@Users.NoReply.GitHub.Com",  # casing varies too
        ]
        keys = {canonicalize_author_email(v) for v in variants}
        assert keys == {"jane@users.noreply.github.com"}

    def test_different_logins_stay_distinct(self) -> None:
        assert canonicalize_author_email(
            "1+jane@users.noreply.github.com"
        ) != canonicalize_author_email("2+john@users.noreply.github.com")

    def test_real_email_is_lowercased_but_unchanged(self) -> None:
        assert canonicalize_author_email("Jane@Company.COM") == "jane@company.com"

    def test_github_system_author_is_not_folded_into_a_person(self) -> None:
        # noreply@github.com is the GitHub *system* author on some merge commits;
        # it must stay its own bucket, never merged onto a human login.
        assert canonicalize_author_email("noreply@github.com") == "noreply@github.com"

    def test_empty_and_none_pass_through(self) -> None:
        assert canonicalize_author_email("") == ""
        assert canonicalize_author_email(None) is None


class TestOwnerKey:
    def test_noreply_variants_key_to_one_contributor(self) -> None:
        a = owner_key("Jane", "12345+jane@users.noreply.github.com")
        b = owner_key("Jane", "jane@users.noreply.github.com")
        assert a == b == "jane@users.noreply.github.com"

    def test_name_fallback_when_no_email(self) -> None:
        assert owner_key("Jane Doe", None) == "name:Jane Doe"

    def test_real_email_preferred_and_lowercased(self) -> None:
        assert owner_key("Jane", "Jane@Company.com") == "jane@company.com"


class TestCommitExperienceKey:
    def test_noreply_variants_share_an_experience_tally(self) -> None:
        assert author_identity_key(
            "Jane", "1+jane@users.noreply.github.com"
        ) == author_identity_key("Jane", "999+jane@users.noreply.github.com")


class TestIdentityResolver:
    def test_same_name_real_and_noreply_collapse_to_the_real_email(self) -> None:
        # The DoD case: one real-email commit + one noreply commit, same display
        # name, spread across records -> a single contributor keyed on the real
        # email.
        resolve = build_identity_resolver(
            [
                ("Jane Doe", "jane@company.com"),
                ("Jane Doe", "12345+jane@users.noreply.github.com"),
            ]
        )
        assert resolve("Jane Doe", "jane@company.com") == "jane@company.com"
        assert resolve("Jane Doe", "12345+jane@users.noreply.github.com") == "jane@company.com"

    def test_noreply_only_person_keeps_the_noreply_identity(self) -> None:
        resolve = build_identity_resolver([("Ghost", "5+ghost@users.noreply.github.com")])
        assert (
            resolve("Ghost", "5+ghost@users.noreply.github.com") == "ghost@users.noreply.github.com"
        )

    def test_ambiguous_name_with_two_real_emails_is_not_folded(self) -> None:
        # A display name that maps to two different real emails is left split —
        # we can't safely guess which the noreply commit belongs to.
        resolve = build_identity_resolver(
            [
                ("Admin", "a@x.com"),
                ("Admin", "b@y.com"),
                ("Admin", "1+admin@users.noreply.github.com"),
            ]
        )
        assert (
            resolve("Admin", "1+admin@users.noreply.github.com") == "admin@users.noreply.github.com"
        )

    def test_different_names_are_not_bridged(self) -> None:
        # Real email under one name, noreply under another -> stays split (the
        # name<->login bridge is out of scope for the simple version).
        resolve = build_identity_resolver(
            [
                ("Jane Doe", "jane@company.com"),
                ("jdoe", "12345+jdoe@users.noreply.github.com"),
            ]
        )
        assert (
            resolve("jdoe", "12345+jdoe@users.noreply.github.com")
            == "jdoe@users.noreply.github.com"
        )


# ---------------------------------------------------------------------------
# Union-find merge (E3-E5) and its guardrails
# ---------------------------------------------------------------------------

_NR = "@users.noreply.github.com"


def _groups(pairs, evidence=()):
    """The resolver's partition of the given emails, as a set of frozensets."""
    resolve = build_identity_resolver(pairs, evidence)
    out: dict[str, set[str]] = {}
    for name, email in pairs:
        out.setdefault(resolve(name, email), set()).add(email.lower())
    return {frozenset(g) for g in out.values()}, resolve


MERGE_CASES = [
    pytest.param(
        # A handle used as the name on several real emails, and a full name on
        # the login's noreply address: one person (E4 + E2).
        [
            ("Maya Rossi", f"mrossi{_NR}"),
            ("mrossi", f"12381+mrossi{_NR}"),
            ("mrossi", "maya.rossi@gmail.com"),
            ("mrossi", "maya@acme.io"),
        ],
        [{f"mrossi{_NR}", f"12381+mrossi{_NR}", "maya.rossi@gmail.com", "maya@acme.io"}],
        "Maya Rossi",
        id="login-named-real-emails",
    ),
    pytest.param(
        [
            ("Omar Haddad", f"5358+ohdev{_NR}"),
            ("ohdev", f"5358+ohdev{_NR}"),
            ("ohdev", "omar.h@gmail.com"),
        ],
        [{f"5358+ohdev{_NR}", "omar.h@gmail.com"}],
        "Omar Haddad",
        id="noreply-full-name-plus-handle-email",
    ),
    pytest.param(
        # Machine-local addresses fold into the one real email of that name (E5).
        [
            ("Lena Park", "lena@acme.io"),
            ("Lena Park", "lenapark@Lenas-MacBook-Pro.local"),
            ("Lena Park", "lenapark@mac.local.meter"),
            ("Lena Park", "lena@(none)"),
        ],
        [
            {
                "lena@acme.io",
                "lenapark@lenas-macbook-pro.local",
                "lenapark@mac.local.meter",
                "lena@(none)",
            }
        ],
        "Lena Park",
        id="machine-local-emails",
    ),
    pytest.param(
        # Same full name on noreply + one real email (E3), a local email (E5).
        [
            ("Tomas Berg", f"144+tberg{_NR}"),
            ("Tomas Berg", "tomas@acme.io"),
            ("Tomas Berg", "tomas@Tomass-MacBook-Pro-2.local"),
        ],
        [{f"144+tberg{_NR}", "tomas@acme.io", "tomas@tomass-macbook-pro-2.local"}],
        "Tomas Berg",
        id="full-name-noreply-and-local",
    ),
    pytest.param(
        # Two different people called Alex keep their own identities, and so
        # do two real emails under one full name: a name alone never merges.
        [
            ("Alex", "alex@one.com"),
            ("Alex", "alex@two.org"),
            ("Alex Kim", "akim@one.com"),
            ("Alex Kim", "alex.kim@two.org"),
        ],
        [{"alex@one.com"}, {"alex@two.org"}, {"akim@one.com"}, {"alex.kim@two.org"}],
        None,
        id="two-alexes-stay-apart",
    ),
    pytest.param(
        # A one-word name is too common to bridge a noreply login (E3/E5 need
        # 2+ words); the login is not that email's name either (no E4).
        [
            ("Kirill", f"80+kbast{_NR}"),
            ("Kirill", "kirill@other.pw"),
            ("Kirill", "kirill@laptop.local"),
        ],
        [{f"80+kbast{_NR}"}, {"kirill@other.pw"}, {"kirill@laptop.local"}],
        None,
        id="one-word-name-does-not-merge",
    ),
    pytest.param(
        # Generic and short logins never bridge by name.
        [
            ("admin", f"9+admin{_NR}"),
            ("admin", "ops@acme.io"),
            ("ubuntu", f"ubuntu{_NR}"),
            ("ubuntu", "ci@acme.io"),
            ("jo", f"3+jo{_NR}"),
            ("jo", "jo@acme.io"),
        ],
        [
            {f"9+admin{_NR}"},
            {"ops@acme.io"},
            {f"ubuntu{_NR}"},
            {"ci@acme.io"},
            {f"3+jo{_NR}"},
            {"jo@acme.io"},
        ],
        None,
        id="generic-login-collision",
    ),
    pytest.param(
        # A login that is the name of more than 3 real emails is a common word.
        [
            ("mike", f"7+mike{_NR}"),
            ("mike", "mike@a.com"),
            ("mike", "mike@b.com"),
            ("mike", "mike@c.com"),
            ("mike", "mike@d.com"),
        ],
        [{f"7+mike{_NR}"}, {"mike@a.com"}, {"mike@b.com"}, {"mike@c.com"}, {"mike@d.com"}],
        None,
        id="login-naming-too-many-emails",
    ),
    pytest.param(
        # Two different logins would land in one identity: roll the soft edges
        # back rather than guess which login is this person's.
        [
            ("Jane Doe", f"11+janed{_NR}"),
            ("Jane Doe", f"22+jdoe99{_NR}"),
            ("Jane Doe", "jane@corp.com"),
        ],
        [{f"11+janed{_NR}"}, {f"22+jdoe99{_NR}"}, {"jane@corp.com"}],
        None,
        id="two-login-conflict-rolls-back",
    ),
    pytest.param(
        # Same conflict reached through E4: two handles name one real email.
        [
            ("Pat Quinn", f"31+pquinn{_NR}"),
            ("Patrick", f"32+patq{_NR}"),
            ("pquinn", "pat@corp.com"),
            ("patq", "pat@corp.com"),
        ],
        [{f"31+pquinn{_NR}"}, {f"32+patq{_NR}"}, {"pat@corp.com"}],
        None,
        id="two-login-conflict-via-handles",
    ),
    pytest.param(
        # One shared alias committed under two people's names must not bridge
        # them: it matches a different real email per name, so it takes neither.
        [
            ("Jane Doe", f"41+shared{_NR}"),
            ("John Smith", f"41+shared{_NR}"),
            ("Jane Doe", "jane@corp.com"),
            ("John Smith", "john@corp.com"),
            ("Jane Doe", "build@ci-box.local"),
            ("John Smith", "build@ci-box.local"),
        ],
        [{f"41+shared{_NR}"}, {"jane@corp.com"}, {"john@corp.com"}, {"build@ci-box.local"}],
        None,
        id="shared-alias-two-names-stays-apart",
    ),
]


@pytest.mark.parametrize(("pairs", "expected", "display"), MERGE_CASES)
def test_identity_merge_table(pairs, expected, display) -> None:
    groups, resolve = _groups(pairs)
    assert groups == {frozenset(g) for g in expected}
    if display is not None:
        (key,) = {resolve(n, e) for n, e in pairs}
        assert resolve.display_name(key) == display


def test_bots_never_merge_and_are_not_people() -> None:
    pairs = [
        ("dependabot[bot]", f"49699333+dependabot[bot]{_NR}"),
        ("dependabot[bot]", f"dependabot[bot]{_NR}"),
        ("github-actions", "ci@acme.io"),
        ("Dana White", "dana@acme.io"),
    ]
    resolve = build_identity_resolver(pairs)
    bot = resolve("dependabot[bot]", f"49699333+dependabot[bot]{_NR}")
    assert resolve.is_bot(bot)
    assert resolve.is_bot(resolve("github-actions", "ci@acme.io"))
    assert resolve.people() == {"dana@acme.io"}


def test_co_author_trailer_alone_never_merges() -> None:
    # Only a trailer names the real email with the handle, so E4 has no author
    # row to stand on and the two stay apart. The trailer's name still counts
    # for the display name of the identity it points at.
    pairs = [("Ana Lima", "ana@a.io"), ("ana-l", f"7+ana-l{_NR}")]
    groups, resolve = _groups(pairs, evidence=[("ana-l", "ana@a.io"), ("Ana M. Lima", "ana@a.io")])
    assert groups == {frozenset({"ana@a.io"}), frozenset({f"7+ana-l{_NR}"})}
    assert resolve.display_name("ana@a.io") == "Ana Lima"


def test_display_name_prefers_a_full_name() -> None:
    resolve = build_identity_resolver([("mrossi", "m@acme.io")] * 5 + [("Maya Rossi", "m@acme.io")])
    assert resolve.display_name("m@acme.io") == "Maya Rossi"


# ---------------------------------------------------------------------------
# Whole-repo: one git log pass, trailers, and the truck factor
# ---------------------------------------------------------------------------


def _git_commit(repo: Path, author: str, message: str) -> None:
    (repo / "f.txt").write_text(f"{author}\n{message}\n")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    env = {**os.environ, "GIT_COMMITTER_NAME": "Committer", "GIT_COMMITTER_EMAIL": "c@x.io"}
    subprocess.run(
        ["git", "commit", "-q", f"--author={author}", "-m", message],
        cwd=repo,
        check=True,
        env=env,
    )


def test_contributor_count_from_one_log_pass(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    authors = [
        f"Maya Rossi <12381+mrossi{_NR}>",
        "mrossi <maya.rossi@gmail.com>",
        "Lena Park <lena@acme.io>",
        "Lena Park <lenapark@Lenas-MacBook-Pro.local>",
        "Alex <alex@one.com>",
        "Alex <alex@two.org>",
        f"dependabot[bot] <49699333+dependabot[bot]{_NR}>",
        "Ana Lima <ana@a.io>",
    ]
    for n, author in enumerate(authors):
        _git_commit(tmp_path, author, f"change {n}")
    # The trailer pairs the handle with Ana's real email; it must not merge.
    _git_commit(tmp_path, f"ana-l <7+ana-l{_NR}>", "pair work\n\nCo-authored-by: ana-l <ana@a.io>")

    repo = git.Repo(tmp_path)
    shortlog = repo.git.shortlog("-sn", "HEAD").splitlines()
    # shortlog groups by name: it splits Maya and merges the two Alexes.
    assert len(shortlog) == 7
    # Maya, Lena, two Alexes, Ana, ana-l; the bot is not a person.
    assert count_people(repo, "HEAD") == 6
    assert capture_repo_totals(repo).total_contributor_count == 6


def test_truck_factor_counts_people_not_names() -> None:
    def meta(path: str, name: str, email: str) -> dict:
        return {
            "file_path": path,
            "primary_owner_name": name,
            "primary_owner_email": email,
            "bus_factor": 1,
        }

    rows = (
        [meta(f"a/{i}.py", "Maya Rossi", f"12381+mrossi{_NR}") for i in range(4)]
        + [meta(f"b/{i}.py", "Omar Haddad", "omar@acme.io") for i in range(3)]
        + [meta(f"c/{i}.py", "mrossi", "maya.rossi@gmail.com") for i in range(3)]
        + [meta(f"d/{i}.py", "Lena Park", "lena@acme.io") for i in range(2)]
        + [meta(f"e/{i}.py", "Ivan Petrov", "ivan@acme.io") for i in range(2)]
    )
    # Keyed by name it takes 3 owners (4 + 3 + 3) to pass half of 14 files;
    # as people Maya owns 7 and Omar 3, so 2 do.
    assert build_people(rows)["truck_factor"] == 2
    bots = [meta(f"z/{i}.py", "dependabot[bot]", f"1+dependabot[bot]{_NR}") for i in range(9)]
    people = build_people(rows + bots)
    # Automation owns nothing: the bot's 9 files neither count nor move the factor.
    assert people["owner_count"] == 4
    assert people["truck_factor"] == 2
