"""LanguageSpec for typescript (extracted from the registry data table)."""

from ..spec import LanguageSpec

#: Predeclared / lib.dom / lib.es type names that never resolve to a user-
#: defined symbol in the workspace. Filtering them before the resolver
#: lookup avoids polluting the graph with edges for ubiquitous globals
#: (``string``, ``Promise``, ``Pick``) the dead-code analyzer does not
#: care about. The list intentionally errs on the side of inclusion: a
#: user type colliding with one of these names will fail to resolve via
#: the type-ref path, but cross-file usage still surfaces through the
#: value-import + call path.
#:
#: Exported because javascript shares the TS extractor and needs the same set.
BUILTIN_TYPES: frozenset[str] = frozenset(
    {
        # Primitives + structural
        "string", "number", "boolean", "bigint", "symbol",
        "void", "null", "undefined", "never", "unknown", "any",
        "object", "this", "Object",
        # Built-in containers / wrappers. ``Map`` / ``Set`` / ``WeakMap``
        # / ``WeakSet`` are intentionally **not** listed: they're routinely
        # shadowed by user-defined types (Hono ``interface Set<E>`` /
        # ``interface Get<E>`` is the canonical case) and filtering them
        # at extraction time hides the same-file rescue.
        "Array", "ReadonlyArray", "Promise", "Awaited", "WeakRef",
        "Date", "RegExp", "Error", "TypeError", "RangeError",
        "SyntaxError", "ReferenceError", "EvalError",
        "Function", "CallableFunction", "NewableFunction",
        "ArrayBuffer", "SharedArrayBuffer", "DataView",
        "Int8Array", "Uint8Array", "Uint8ClampedArray",
        "Int16Array", "Uint16Array", "Int32Array", "Uint32Array",
        "Float32Array", "Float64Array", "BigInt64Array", "BigUint64Array",
        "Iterable", "AsyncIterable", "Iterator", "AsyncIterator",
        "IterableIterator", "AsyncIterableIterator",
        "Generator", "AsyncGenerator", "GeneratorFunction",
        "Proxy", "Reflect", "JSON", "Math",
        # Utility types
        "Record", "Partial", "Required", "Readonly",
        "Pick", "Omit", "Exclude", "Extract", "NonNullable",
        "Parameters", "ConstructorParameters", "ReturnType",
        "InstanceType", "ThisType", "ThisParameterType", "OmitThisParameter",
        "Uppercase", "Lowercase", "Capitalize", "Uncapitalize",
        # Common DOM / Node globals that show up everywhere as parameter
        # types — listing here is a perf optimisation, not correctness.
        "URL", "URLSearchParams", "Request", "Response", "Headers",
        "Blob", "File", "FormData", "FileReader",
        "AbortController", "AbortSignal", "AbortError",
        "EventTarget", "Event", "CustomEvent", "MessageEvent",
        "Element", "HTMLElement", "Node", "Document", "Window",
        "Buffer", "NodeJS",
    }
)

SPEC = LanguageSpec(
    tag="typescript",
    display_name="TypeScript",
    import_support="full",
    test_infixes=(".test.", ".spec."),
    test_fixture_stems=("fixtures", "fixture", "setup-tests"),
    test_stem_suffixes=("_test", ".test", ".spec"),
    extensions=frozenset({".ts", ".tsx", ".mts", ".cts"}),
    grammar_package="tree_sitter_typescript",
    grammar_loader="language_typescript",
    scm_file="typescript.scm",
    heritage_node_types=frozenset(
        {"class_declaration", "abstract_class_declaration", "interface_declaration"}
    ),
    entry_point_patterns=("index.ts", "main.ts", "app.ts", "server.ts"),
    manifest_files=("package.json",),
    lock_files=("package-lock.json", "yarn.lock", "pnpm-lock.yaml"),
    generated_suffixes=("_pb.ts",),
    shebang_tokens=(),
    blocked_dirs=("node_modules", ".next", "dist", "build"),
    builtin_calls=frozenset(
        {
            "parseInt",
            "parseFloat",
            "isNaN",
            "isFinite",
            "decodeURI",
            "decodeURIComponent",
            "encodeURI",
            "encodeURIComponent",
            "setTimeout",
            "setInterval",
            "clearTimeout",
            "clearInterval",
            "fetch",
            "require",
            "eval",
            "atob",
            "btoa",
            "JSON",
            "Math",
            "console",
            "Reflect",
            "Proxy",
            "Object",
            "Array",
            "String",
            "Number",
            "Boolean",
            "Date",
            "RegExp",
            "Promise",
            "Set",
            "Map",
            "WeakMap",
            "WeakSet",
            "Symbol",
            "ArrayBuffer",
            "DataView",
            "Uint8Array",
            "Error",
            "TypeError",
            "RangeError",
            "SyntaxError",
            "ReferenceError",
            "Int8Array",
            "Int16Array",
            "Int32Array",
            "Float32Array",
            "Float64Array",
        }
    ),
    builtin_parents=frozenset({"Error", "Object"}),
    builtin_types=BUILTIN_TYPES,
    color_hex="#3178C6",
)
