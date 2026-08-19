"""Tests for GitHub noreply author-identity canonicalization.

Covers the identity chokepoints that split one person into several contributor
buckets: the shared ``canonicalize_author_email`` helper, the ``owner_key``
that keys the contributor directory, the commit-experience tally key, and the
per-file author-email selection in ``index_file``.
"""

from __future__ import annotations

from repowise.core.ingestion.git_indexer import (
    build_identity_resolver,
    canonicalize_author_email,
)
from repowise.core.ingestion.git_indexer.identity import author_identity_key
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
