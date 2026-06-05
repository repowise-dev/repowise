"""LanguageSpec for objectivec (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="objectivec",
    display_name="Objective-C",
    extensions=frozenset({".m", ".mm"}),
    # main.m holds UIApplicationMain/NSApplicationMain.
    entry_point_patterns=("main.m",),
    is_passthrough=True,
)
