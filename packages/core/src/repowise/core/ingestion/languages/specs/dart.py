"""LanguageSpec for dart (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="dart",
    display_name="Dart",
    # package:test convention: test/foo_test.dart.
    test_stem_suffixes=("_test",),
    entry_point_patterns=("main.dart",),
    manifest_files=("pubspec.yaml",),
    extensions=frozenset({".dart"}),
    is_passthrough=True,
    # Lightweight regex resolver: package:/relative URIs, part/part of, export.
    import_support="partial",
)
