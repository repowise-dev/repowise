"""LanguageSpec for asciidoc.

AsciiDoc READMEs (README.asciidoc / README.adoc) are the convention in
much of the Erlang/Elixir ecosystem (cowlib, cowboy, ranch); without a
spec they are not indexed at all and the tour's overview step loses its
root README target.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="asciidoc",
    display_name="AsciiDoc",
    extensions=frozenset({".adoc", ".asciidoc"}),
    is_code=False,
    is_passthrough=True,
)
