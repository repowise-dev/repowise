"""LanguageSpec for astro.

An ``.astro`` component is parsed as TypeScript: ``sfc_source`` blanks the
markup and ``<style>`` so the ``---`` frontmatter and every ``<script>`` body
sit at byte-identical offsets in a valid TS buffer. Hence
``shares_grammar_with`` and ``scm_file`` both point at typescript. There is no
tree-sitter-astro on PyPI, so the regions are located by a byte scan, as for
Razor.
"""

from ..spec import LanguageSpec
from .typescript import SPEC as _TS

SPEC = LanguageSpec(
    tag="astro",
    display_name="Astro",
    import_support="full",
    test_infixes=(".test.", ".spec."),
    extensions=frozenset({".astro"}),
    shares_grammar_with="typescript",
    scm_file="typescript.scm",
    heritage_node_types=_TS.heritage_node_types,
    manifest_files=("package.json", "astro.config.mjs", "astro.config.ts"),
    # Framework config, not a package declaration — package.json is the root.
    build_config_manifests=("astro.config.mjs", "astro.config.ts"),
    lock_files=_TS.lock_files,
    blocked_dirs=("node_modules", ".astro", "dist"),
    # The frontmatter and scripts are TypeScript, filtered like a sibling .ts file.
    builtin_calls=_TS.builtin_calls,
    builtin_parents=_TS.builtin_parents,
    builtin_types=_TS.builtin_types,
    color_hex="#BC52EE",
)
