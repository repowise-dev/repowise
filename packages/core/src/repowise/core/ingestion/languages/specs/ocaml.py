"""LanguageSpec for ocaml (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="ocaml",
    display_name="OCaml",
    extensions=frozenset({".ml", ".mli"}),
    # dune executables conventionally live in bin/main.ml.
    entry_point_patterns=("main.ml",),
    is_passthrough=True,
)
