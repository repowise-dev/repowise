"""The "no AST parser and no structure to extract" language set has one home.

Three modules computed this comprehension verbatim -- the parser's passthrough
skip, the traverser's generated-marker skip, and dead code's non-code
exemption -- and they had already drifted on whether ``unknown`` belonged.
Same failure mode ``core.test_paths`` was created to end, so the same fix: one
spelling on the registry, and these tests keep the call sites pinned to it.
"""

from __future__ import annotations

from repowise.core.analysis.dead_code.constants import _NON_CODE_LANGUAGES
from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.ingestion.parser import _PASSTHROUGH_LANGUAGES
from repowise.core.ingestion.traverser import _SKIP_GENERATED_CHECK


def _reference_set() -> frozenset[str]:
    """The predicate as it read at all three call sites before extraction."""
    return frozenset(
        spec.tag
        for spec in REGISTRY.all_specs()
        if spec.is_passthrough
        and (not spec.is_code or spec.is_infra)
        and spec.tag not in ("openapi", "unknown")
    )


def test_registry_set_matches_the_original_predicate():
    """Extraction was behaviour-preserving, not a redefinition."""
    assert REGISTRY.unparseable_data_languages() == _reference_set()


def test_parser_and_traverser_use_the_shared_set():
    assert REGISTRY.unparseable_data_languages() == _PASSTHROUGH_LANGUAGES
    assert REGISTRY.unparseable_data_languages() == _SKIP_GENERATED_CHECK


def test_dead_code_adds_unknown_and_says_so():
    """Dead code alone treats ``unknown`` as non-code.

    An unidentified language is no evidence of reachability either way, so
    flagging it would be a guess. That is a real difference from the parser's
    question, which is why it is a second named method rather than a third
    copy of the comprehension.
    """
    assert REGISTRY.unparseable_or_unknown_languages() == _NON_CODE_LANGUAGES
    assert REGISTRY.unparseable_data_languages() | {"unknown"} == _NON_CODE_LANGUAGES


def test_unknown_is_excluded_from_the_base_set():
    """Regression pin: ``unknown`` is itself a passthrough spec.

    Deriving the base set by excluding only ``openapi`` silently pulls
    ``unknown`` in, which would make the parser treat unidentified files as
    passthrough and stop the traverser reading their generated-file markers.
    """
    assert "unknown" not in REGISTRY.unparseable_data_languages()
    assert "unknown" in REGISTRY.unparseable_or_unknown_languages()


def test_real_data_formats_are_in_the_set():
    """Anchor the set to concrete members so an upstream spec flip is caught."""
    for tag in ("markdown", "json", "yaml", "toml"):
        assert tag in REGISTRY.unparseable_data_languages(), tag


def test_passthrough_code_languages_are_not_in_the_set():
    """Languages awaiting a grammar are NOT "data".

    clojure/haskell are ``is_passthrough`` but real code: zero symbols
    there is a gap in us, not a property of the file, so they must stay
    eligible for the analyses this set exempts. Elixir and Objective-C used to
    sit here and now parse to an AST, which is where a language in this state
    is headed.
    """
    for tag in ("clojure", "haskell"):
        assert tag in REGISTRY.passthrough_languages(), tag
        assert tag not in REGISTRY.unparseable_data_languages(), tag
