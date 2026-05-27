"""LanguageSpec for shell (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="shell",
    display_name="Shell",
    extensions=frozenset({".sh", ".bash", ".zsh"}),
    is_infra=True,
    is_passthrough=True,
    shebang_tokens=("bash", " sh"),
    color_hex="#89E051",
)
