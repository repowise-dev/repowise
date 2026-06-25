"""LanguageSpec for lean (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="lean",
    display_name="Lean 4",
    entry_point_patterns=("Main.lean",),
    # Lake build manifests / toolchain pin — package plumbing, not domain code.
    manifest_files=("lakefile.lean", "lakefile.toml", "lean-toolchain"),
    extensions=frozenset({".lean"}),
    is_passthrough=True,
    # Lightweight regex resolver: import/open statements → module-name index.
    import_support="partial",
)
