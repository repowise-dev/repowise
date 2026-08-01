"""LanguageSpec for vue.

A ``.vue`` component is parsed as TypeScript: ``sfc_source`` blanks the
``<template>`` markup and ``<style>`` so the ``<script>`` blocks and the markup
expressions sit at byte-identical offsets in a valid TS buffer. Hence
``shares_grammar_with`` and ``scm_file`` both point at typescript — the markup
grammar (tree-sitter-html, which parses an SFC cleanly) is loaded separately by
``sfc_source`` and only locates regions, it extracts nothing.

Ceiling: an Options-API member spelled ``foo: function () {}`` or
``foo: () => {}`` inside ``methods``/``computed``/``watch`` is not captured,
because ``typescript.scm`` has no pattern for a ``pair`` with a function value.
The shorthand ``foo() {}`` spelling *is* captured — it parses as a
``method_definition`` — which covers 1588 of 1592 member functions (99.7%)
across the 275 Options-API files in the validation corpus. Lifting the
remaining 0.3% belongs in ``typescript.scm``, not here: the gap is a general
TS/JS one that affects every object literal in every ``.ts``/``.js`` file.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="vue",
    display_name="Vue",
    import_support="full",
    test_infixes=(".test.", ".spec."),
    extensions=frozenset({".vue"}),
    shares_grammar_with="typescript",
    scm_file="typescript.scm",
    heritage_node_types=frozenset(
        {"class_declaration", "abstract_class_declaration", "interface_declaration"}
    ),
    # Nuxt's filesystem router and the conventional SPA root: loaded by
    # convention, never imported, so they anchor reachability for everything
    # they pull in.
    entry_point_patterns=("App.vue", "app.vue", "error.vue", "default.vue"),
    manifest_files=("package.json", "vite.config.js", "nuxt.config.ts"),
    lock_files=("package-lock.json", "yarn.lock", "pnpm-lock.yaml"),
    blocked_dirs=("node_modules", ".nuxt", ".output", "dist", "build"),
    # Vue's compiler macros and template intrinsics. These are compiler
    # intrinsics, not user functions — they must never mint a call edge.
    builtin_calls=frozenset(
        {
            "defineProps",
            "defineEmits",
            "defineExpose",
            "defineOptions",
            "defineSlots",
            "defineModel",
            "withDefaults",
            "ref",
            "reactive",
            "computed",
            "watch",
            "watchEffect",
            "onMounted",
            "onUnmounted",
            "onBeforeMount",
            "onBeforeUnmount",
            "onUpdated",
            "nextTick",
            "provide",
            "inject",
            "toRef",
            "toRefs",
            "unref",
            "shallowRef",
            "markRaw",
            "useSlots",
            "useAttrs",
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
    color_hex="#42B883",
)
