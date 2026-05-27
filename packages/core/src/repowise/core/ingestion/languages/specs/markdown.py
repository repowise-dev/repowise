"""LanguageSpec for markdown (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="markdown",
    display_name="Markdown",
    extensions=frozenset({".md", ".mdx"}),
    is_code=False,
    is_passthrough=True,
)
