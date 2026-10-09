"""LanguageSpec for astro.

An ``.astro`` component is parsed as TypeScript: ``sfc_source`` blanks the
markup and ``<style>`` so the ``---`` frontmatter and every ``<script>`` body
sit at byte-identical offsets in a valid TS buffer. Hence
``shares_grammar_with`` and ``scm_file`` both point at typescript. There is no
tree-sitter-astro on PyPI, so the regions are located by a byte scan, as for
Razor.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="astro",
    display_name="Astro",
    import_support="full",
    test_infixes=(".test.", ".spec."),
    extensions=frozenset({".astro"}),
    shares_grammar_with="typescript",
    scm_file="typescript.scm",
    heritage_node_types=frozenset(
        {"class_declaration", "abstract_class_declaration", "interface_declaration"}
    ),
    manifest_files=("package.json", "astro.config.mjs", "astro.config.ts"),
    # Framework config, not a package declaration — package.json is the root.
    build_config_manifests=("astro.config.mjs", "astro.config.ts"),
    lock_files=("package-lock.json", "yarn.lock", "pnpm-lock.yaml"),
    blocked_dirs=("node_modules", ".astro", "dist"),
    builtin_calls=frozenset(
        {
            "console",
            "JSON",
            "Math",
            "Object",
            "Array",
            "String",
            "Number",
            "Boolean",
            "Date",
            "Promise",
            "Set",
            "Map",
            "Error",
            "fetch",
            "setTimeout",
            "clearTimeout",
            "setInterval",
            "clearInterval",
            "parseInt",
            "parseFloat",
        }
    ),
    builtin_parents=frozenset({"Error", "Object"}),
    color_hex="#BC52EE",
)
