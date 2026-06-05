"""LanguageSpec for dart (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="dart",
    display_name="Dart",
    # package:test convention: test/foo_test.dart.
    test_stem_suffixes=("_test",),
    extensions=frozenset({".dart"}),
    is_passthrough=True,
)
