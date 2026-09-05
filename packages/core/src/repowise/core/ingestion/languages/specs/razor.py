"""LanguageSpec for razor.

A ``.razor`` or ``.cshtml`` file is two languages in one file: C# regions
(``@code`` / ``@functions`` / ``@{ }`` blocks) plus Razor markup. There is no
``tree-sitter-razor`` on PyPI, so ``sfc_source`` byte-scans the file for the
C#-bearing regions, blanks everything else to spaces at byte-identical
offsets, and the result is parsed with the ordinary C# grammar. Hence
``shares_grammar_with`` and ``scm_file`` both point at csharp, mirroring how
``specs/svelte.py`` projects to TypeScript.

The C# builtin / heritage surfaces are copied from the csharp spec so the
projection is filtered exactly like a real ``.cs`` file would be.
"""

from __future__ import annotations

from ..spec import LanguageSpec
from .csharp import SPEC as _CSHARP

SPEC = LanguageSpec(
    tag="razor",
    display_name="Razor",
    # Razor's own using directives (``@using X``) are not projected yet, so
    # no import edges are emitted from these files today and the resolver
    # tier stays at the honest default rather than claiming C#'s full
    # resolution for edges that never exist.
    import_support="none",
    extensions=frozenset({".razor", ".cshtml"}),
    shares_grammar_with="csharp",
    scm_file="csharp.scm",
    # ``@inherits`` is blanked and the projected C# holds no type
    # declarations, so there is nothing for a heritage extractor to read.
    heritage_node_types=frozenset(),
    manifest_files=_CSHARP.manifest_files,
    # None of these declare a package; the .csproj does. Copied from the
    # csharp spec so a solution dir holding only MSBuild/NuGet settings is
    # not mistaken for a package root.
    build_config_manifests=_CSHARP.build_config_manifests,
    lock_files=_CSHARP.lock_files,
    generated_suffixes=_CSHARP.generated_suffixes,
    blocked_dirs=_CSHARP.blocked_dirs,
    builtin_calls=_CSHARP.builtin_calls,
    builtin_parents=_CSHARP.builtin_parents,
    builtin_types=_CSHARP.builtin_types,
    color_hex="#512BD4",
)
