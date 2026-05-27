"""LanguageSpec for makefile (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="makefile",
    display_name="Makefile",
    special_filenames=frozenset({"Makefile", "makefile", "GNUmakefile"}),
    is_infra=True,
    is_passthrough=True,
    color_hex="#427819",
)
