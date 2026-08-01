"""LanguageSpec for svelte.

A ``.svelte`` component is parsed as TypeScript: ``sfc_source`` blanks the
markup and ``<style>`` so the ``<script>`` blocks and markup expressions sit at
byte-identical offsets in a valid TS buffer. Hence ``shares_grammar_with`` and
``scm_file`` both point at typescript — the Svelte grammar itself is loaded
separately by ``sfc_source`` and only locates regions, it extracts nothing.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="svelte",
    display_name="Svelte",
    import_support="full",
    test_infixes=(".test.", ".spec."),
    extensions=frozenset({".svelte"}),
    shares_grammar_with="typescript",
    scm_file="typescript.scm",
    heritage_node_types=frozenset(
        {"class_declaration", "abstract_class_declaration", "interface_declaration"}
    ),
    # SvelteKit's filesystem router: these are loaded by convention, never
    # imported, so they anchor reachability for everything they pull in.
    entry_point_patterns=("+page.svelte", "+layout.svelte", "+error.svelte", "App.svelte"),
    manifest_files=("package.json", "svelte.config.js"),
    lock_files=("package-lock.json", "yarn.lock", "pnpm-lock.yaml"),
    blocked_dirs=("node_modules", ".svelte-kit", "dist", "build"),
    # Svelte 5 runes and the template builtins. These are compiler intrinsics,
    # not user functions — they must never mint a call edge.
    builtin_calls=frozenset(
        {
            "$state",
            "$derived",
            "$effect",
            "$props",
            "$bindable",
            "$inspect",
            "$host",
            "onMount",
            "onDestroy",
            "beforeUpdate",
            "afterUpdate",
            "tick",
            "setContext",
            "getContext",
            "hasContext",
            "createEventDispatcher",
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
    color_hex="#FF3E00",
)
