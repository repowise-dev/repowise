"""LanguageSpec for ocaml (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="ocaml",
    display_name="OCaml",
    extensions=frozenset({".ml", ".mli"}),
    is_passthrough=True,
)
