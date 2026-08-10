"""The shared identifier slug both name-producing detectors run through.

It exists so ``suggested_name`` means one thing across the payload rather than
one thing per detector.
"""

from __future__ import annotations

from repowise.core.analysis.health.refactoring.naming import identifier_slug


def test_lowercases_and_keeps_alphanumerics():
    assert identifier_slug("Providers") == "providers"
    assert identifier_slug("http2") == "http2"


def test_collapses_non_alphanumeric_runs_to_single_underscores():
    assert identifier_slug("api-client") == "api_client"
    assert identifier_slug("a..--..b") == "a_b"
    assert identifier_slug("pkg/sub") == "pkg_sub"


def test_strips_leading_and_trailing_separators():
    assert identifier_slug("--api--") == "api"
    assert identifier_slug("_private_") == "private"


def test_leading_digit_is_made_identifier_safe():
    # Not a valid identifier start in most languages.
    assert identifier_slug("3d") == "_3d"
    assert identifier_slug("2fa-token") == "_2fa_token"


def test_unusable_input_returns_empty_so_callers_can_pick_a_fallback():
    assert identifier_slug("") == ""
    assert identifier_slug(None) == ""
    assert identifier_slug("---") == ""
    assert identifier_slug("...") == ""
