"""Central language registry — single source of truth.

Every language-specific constant previously scattered across models.py,
parser.py, language_data.py, traverser.py, page_generator.py, cli/ui.py,
git_indexer.py, and others is consolidated here.

This module is a **leaf dependency** — it imports nothing from the
ingestion pipeline (no parser, graph, traverser, etc.) to avoid circular
imports.

Frontend language colours are maintained in parallel in
``packages/web/src/lib/utils/confidence.ts`` and
``packages/web/src/components/``.  A Phase 2 build task will generate
the TypeScript file from this registry.
"""

from __future__ import annotations

from collections.abc import Iterable

from .spec import LanguageSpec

# =========================================================================
# Language specifications — one per LanguageTag value
# =========================================================================

_SPECS: tuple[LanguageSpec, ...] = (
    # -----------------------------------------------------------------
    # Full-tier languages (AST + imports + calls + heritage + bindings)
    # -----------------------------------------------------------------
    LanguageSpec(
        tag="python",
        display_name="Python",
        extensions=frozenset({".py", ".pyi"}),
        grammar_package="tree_sitter_python",
        scm_file="python.scm",
        heritage_node_types=frozenset({"class_definition"}),
        entry_point_patterns=(
            "main.py",
            "app.py",
            "__main__.py",
            "manage.py",
            "wsgi.py",
            "asgi.py",
        ),
        manifest_files=("pyproject.toml", "setup.py", "setup.cfg"),
        lock_files=("poetry.lock", "uv.lock"),
        generated_suffixes=("_pb2.py", "_pb2_grpc.py"),
        shebang_tokens=("python",),
        blocked_dirs=(
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".tox",
            ".eggs",
            "site-packages",
        ),
        blocked_extensions=(".pyc", ".pyo", ".pyd"),
        builtin_calls=frozenset(
            {
                "abs",
                "aiter",
                "all",
                "anext",
                "any",
                "ascii",
                "bin",
                "bool",
                "breakpoint",
                "bytearray",
                "bytes",
                "callable",
                "chr",
                "classmethod",
                "compile",
                "complex",
                "delattr",
                "dict",
                "dir",
                "divmod",
                "enumerate",
                "eval",
                "exec",
                "filter",
                "float",
                "format",
                "frozenset",
                "getattr",
                "globals",
                "hasattr",
                "hash",
                "help",
                "hex",
                "id",
                "input",
                "int",
                "isinstance",
                "issubclass",
                "iter",
                "len",
                "list",
                "locals",
                "map",
                "max",
                "memoryview",
                "min",
                "next",
                "object",
                "oct",
                "open",
                "ord",
                "pow",
                "print",
                "property",
                "range",
                "repr",
                "reversed",
                "round",
                "set",
                "setattr",
                "slice",
                "sorted",
                "staticmethod",
                "str",
                "sum",
                "super",
                "tuple",
                "type",
                "vars",
                "zip",
                "__import__",
            }
        ),
        builtin_parents=frozenset(
            {
                "object",
                "Exception",
                "BaseException",
                "type",
                "ABC",
                "ABCMeta",
                "Protocol",
                "NamedTuple",
                "TypedDict",
                "Enum",
                "IntEnum",
                "Flag",
                "IntFlag",
            }
        ),
        color_hex="#3572A5",
    ),
    LanguageSpec(
        tag="typescript",
        display_name="TypeScript",
        extensions=frozenset({".ts", ".tsx"}),
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
        color_hex="#3178C6",
    ),
    LanguageSpec(
        tag="javascript",
        display_name="JavaScript",
        extensions=frozenset({".js", ".jsx", ".mjs", ".cjs"}),
        grammar_package="tree_sitter_javascript",
        scm_file="javascript.scm",
        heritage_node_types=frozenset({"class_declaration"}),
        entry_point_patterns=("index.js", "main.js", "app.js", "server.js"),
        manifest_files=("package.json",),
        lock_files=("package-lock.json", "yarn.lock", "pnpm-lock.yaml"),
        generated_suffixes=("_pb.js",),
        shebang_tokens=("node",),
        blocked_dirs=("node_modules", "dist", "build"),
        # JavaScript shares TypeScript's builtin calls
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
        color_hex="#F1E05A",
    ),
    LanguageSpec(
        tag="go",
        display_name="Go",
        extensions=frozenset({".go"}),
        grammar_package="tree_sitter_go",
        scm_file="go.scm",
        heritage_node_types=frozenset({"type_spec"}),
        entry_point_patterns=("main.go", "cmd/main.go"),
        manifest_files=("go.mod",),
        lock_files=("go.sum",),
        generated_suffixes=("_grpc.pb.go",),
        blocked_dirs=("vendor",),
        builtin_calls=frozenset(
            {
                "make",
                "len",
                "cap",
                "new",
                "append",
                "copy",
                "close",
                "delete",
                "complex",
                "real",
                "imag",
                "panic",
                "recover",
                "print",
                "println",
            }
        ),
        builtin_parents=frozenset({"error"}),
        color_hex="#00ADD8",
    ),
    LanguageSpec(
        tag="rust",
        display_name="Rust",
        extensions=frozenset({".rs"}),
        grammar_package="tree_sitter_rust",
        scm_file="rust.scm",
        heritage_node_types=frozenset({"impl_item", "trait_item", "struct_item", "enum_item"}),
        entry_point_patterns=("main.rs", "lib.rs"),
        manifest_files=("Cargo.toml",),
        lock_files=("Cargo.lock",),
        blocked_dirs=("target",),
        builtin_calls=frozenset(
            {
                "println",
                "eprintln",
                "print",
                "eprint",
                "format",
                "format_args",
                "vec",
                "panic",
                "todo",
                "unimplemented",
                "unreachable",
                "assert",
                "assert_eq",
                "assert_ne",
                "debug_assert",
                "debug_assert_eq",
                "debug_assert_ne",
                "cfg",
                "include",
                "include_str",
                "include_bytes",
                "env",
                "option_env",
                "concat",
                "stringify",
                "line",
                "column",
                "file",
                "write",
                "writeln",
            }
        ),
        builtin_parents=frozenset(
            {
                "Error",
                "Display",
                "Debug",
                "Clone",
                "Copy",
                "Default",
                "PartialEq",
                "Eq",
                "PartialOrd",
                "Ord",
                "Hash",
                "Send",
                "Sync",
                "Sized",
                "Unpin",
                "Iterator",
                "IntoIterator",
                "From",
                "Into",
                "TryFrom",
                "TryInto",
                "AsRef",
                "AsMut",
                "Borrow",
                "BorrowMut",
                "Drop",
                "Deref",
                "DerefMut",
                "Add",
                "Sub",
                "Mul",
                "Div",
                "Rem",
                "Neg",
                "Fn",
                "FnMut",
                "FnOnce",
            }
        ),
        color_hex="#DEA584",
    ),
    LanguageSpec(
        tag="java",
        display_name="Java",
        extensions=frozenset({".java"}),
        grammar_package="tree_sitter_java",
        scm_file="java.scm",
        heritage_node_types=frozenset(
            {"class_declaration", "interface_declaration", "enum_declaration"}
        ),
        entry_point_patterns=("Main.java", "Application.java"),
        manifest_files=("pom.xml", "build.gradle", "build.gradle.kts"),
        blocked_dirs=(".gradle",),
        builtin_calls=frozenset(
            {
                "System",
                "Objects",
                "Arrays",
                "Collections",
                "Math",
                "Integer",
                "Long",
                "Double",
                "Float",
                "Boolean",
                "Character",
                "Byte",
                "Short",
                "String",
                "Object",
                "Class",
                "Thread",
                "Throwable",
                "Exception",
                "RuntimeException",
                "Error",
                "StringBuilder",
                "StringBuffer",
            }
        ),
        builtin_parents=frozenset(
            {
                "Object",
                "Throwable",
                "Exception",
                "RuntimeException",
                "Error",
                "Enum",
                "Serializable",
                "Cloneable",
                "Comparable",
                "Iterable",
                "AutoCloseable",
                "Closeable",
            }
        ),
        color_hex="#B07219",
    ),
    # -----------------------------------------------------------------
    # Partial-tier languages (AST + some imports, gaps in calls/bindings)
    # -----------------------------------------------------------------
    LanguageSpec(
        tag="cpp",
        display_name="C++",
        extensions=frozenset({".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx"}),
        grammar_package="tree_sitter_cpp",
        scm_file="cpp.scm",
        heritage_node_types=frozenset({"class_specifier", "struct_specifier"}),
        entry_point_patterns=("main.cpp", "main.cc"),
        builtin_calls=frozenset(
            {
                "printf",
                "scanf",
                "fprintf",
                "sprintf",
                "snprintf",
                "malloc",
                "calloc",
                "realloc",
                "free",
                "sizeof",
                "alignof",
                "typeid",
                "decltype",
                "static_cast",
                "dynamic_cast",
                "const_cast",
                "reinterpret_cast",
                "move",
                "forward",
                "make_shared",
                "make_unique",
                "make_pair",
                "cout",
                "cerr",
                "endl",
            }
        ),
        builtin_parents=frozenset(
            {
                "exception",
                "runtime_error",
                "logic_error",
                "invalid_argument",
                "out_of_range",
                "overflow_error",
                "string",
                "vector",
                "map",
                "set",
                "list",
                "deque",
                "unordered_map",
                "unordered_set",
                "shared_ptr",
                "unique_ptr",
                "weak_ptr",
            }
        ),
        color_hex="#F34B7D",
    ),
    LanguageSpec(
        tag="c",
        display_name="C",
        extensions=frozenset({".c"}),
        shares_grammar_with="cpp",
        scm_file="c.scm",
        heritage_node_types=frozenset(),
        entry_point_patterns=("main.c",),
        builtin_calls=frozenset(
            {
                "printf",
                "scanf",
                "fprintf",
                "sprintf",
                "snprintf",
                "malloc",
                "calloc",
                "realloc",
                "free",
                "memcpy",
                "memset",
                "memmove",
                "memcmp",
                "strlen",
                "strcpy",
                "strncpy",
                "strcat",
                "strcmp",
                "strncmp",
                "sizeof",
                "offsetof",
                "assert",
                "abort",
                "exit",
            }
        ),
        color_hex="#555555",
    ),
    # -----------------------------------------------------------------
    # Traversal-tier languages (scaffolded — grammar not yet wired)
    # -----------------------------------------------------------------
    LanguageSpec(
        tag="kotlin",
        display_name="Kotlin",
        extensions=frozenset({".kt", ".kts"}),
        grammar_package="tree_sitter_kotlin",
        scm_file="kotlin.scm",
        heritage_node_types=frozenset({"class_declaration", "object_declaration"}),
        manifest_files=("build.gradle.kts", "build.gradle"),
        blocked_dirs=(".gradle",),
        builtin_calls=frozenset(
            {
                "println",
                "print",
                "readLine",
                "arrayOf",
                "listOf",
                "mutableListOf",
                "setOf",
                "mutableSetOf",
                "mapOf",
                "mutableMapOf",
                "hashMapOf",
                "lazy",
                "require",
                "check",
                "error",
                "TODO",
                "run",
                "let",
                "also",
                "apply",
                "with",
            }
        ),
        builtin_parents=frozenset(
            {
                "Any",
                "Throwable",
                "Exception",
                "RuntimeException",
                "Error",
                "Enum",
                "Comparable",
                "Iterable",
                "Serializable",
            }
        ),
        color_hex="#A97BFF",
    ),
    LanguageSpec(
        tag="ruby",
        display_name="Ruby",
        extensions=frozenset({".rb"}),
        grammar_package="tree_sitter_ruby",
        scm_file="ruby.scm",
        heritage_node_types=frozenset({"class"}),
        manifest_files=("Gemfile",),
        lock_files=("Gemfile.lock",),
        shebang_tokens=("ruby",),
        builtin_calls=frozenset(
            {
                "puts",
                "print",
                "p",
                "pp",
                "raise",
                "fail",
                "require",
                "require_relative",
                "include",
                "extend",
                "prepend",
                "attr_reader",
                "attr_writer",
                "attr_accessor",
                "lambda",
                "proc",
            }
        ),
        builtin_parents=frozenset(
            {
                "Object",
                "BasicObject",
                "Exception",
                "StandardError",
                "RuntimeError",
                "ScriptError",
                "LoadError",
                "SyntaxError",
                "Comparable",
                "Enumerable",
                "Kernel",
            }
        ),
        color_hex="#CC342D",
    ),
    LanguageSpec(
        tag="csharp",
        display_name="C#",
        extensions=frozenset({".cs"}),
        grammar_package="tree_sitter_c_sharp",
        scm_file="csharp.scm",
        heritage_node_types=frozenset(
            {
                "class_declaration",
                "interface_declaration",
                "struct_declaration",
                "record_declaration",
            }
        ),
        entry_point_patterns=("Program.cs", "Startup.cs"),
        manifest_files=(
            "Directory.Build.props",
            "Directory.Build.targets",
            "Directory.Packages.props",
            "global.json",
            "nuget.config",
            "NuGet.Config",
        ),
        lock_files=("packages.lock.json",),
        generated_suffixes=(
            ".g.cs",
            ".Designer.cs",
            ".AssemblyInfo.cs",
            ".AssemblyAttributes.cs",
            ".g.i.cs",
        ),
        blocked_dirs=("bin", "obj", ".vs", "TestResults", "packages"),
        builtin_calls=frozenset(
            {
                "Console",
                "Math",
                "Convert",
                "String",
                "Object",
                "Array",
                "GC",
                "Environment",
                "Activator",
                "Task",
                "Interlocked",
                "nameof",
                "typeof",
                "sizeof",
                "default",
            }
        ),
        builtin_parents=frozenset(
            {
                "Object",
                "ValueType",
                "Enum",
                "Exception",
                "SystemException",
                "ApplicationException",
                "IDisposable",
                "IEnumerable",
                "IEnumerator",
                "IComparable",
                "ICloneable",
                "IEquatable",
            }
        ),
        color_hex="#178600",
    ),
    LanguageSpec(
        tag="php",
        display_name="PHP",
        extensions=frozenset({".php"}),
        grammar_package="tree_sitter_php",
        grammar_loader="language_php",
        scm_file="php.scm",
        heritage_node_types=frozenset(
            {"class_declaration", "interface_declaration", "enum_declaration"}
        ),
        manifest_files=("composer.json",),
        lock_files=("composer.lock",),
        blocked_dirs=("vendor",),
        builtin_calls=frozenset(
            {
                "echo",
                "print",
                "var_dump",
                "print_r",
                "isset",
                "empty",
                "unset",
                "array",
                "count",
                "strlen",
                "strpos",
                "substr",
                "implode",
                "explode",
                "json_encode",
                "json_decode",
            }
        ),
        builtin_parents=frozenset(
            {
                "stdClass",
                "Exception",
                "RuntimeException",
                "InvalidArgumentException",
                "LogicException",
                "Iterator",
                "IteratorAggregate",
                "Countable",
                "Serializable",
                "JsonSerializable",
                "Stringable",
                "Throwable",
            }
        ),
        color_hex="#4F5D95",
    ),
    LanguageSpec(
        tag="swift",
        display_name="Swift",
        extensions=frozenset({".swift"}),
        grammar_package="tree_sitter_swift",
        scm_file="swift.scm",
        heritage_node_types=frozenset({"class_declaration", "protocol_declaration"}),
        manifest_files=("Package.swift",),
        builtin_calls=frozenset(
            {
                "print",
                "debugPrint",
                "fatalError",
                "precondition",
                "assert",
                "min",
                "max",
                "abs",
                "stride",
                "zip",
                "map",
                "filter",
                "reduce",
                "sorted",
            }
        ),
        builtin_parents=frozenset(
            {
                "NSObject",
                "Codable",
                "Encodable",
                "Decodable",
                "Hashable",
                "Equatable",
                "Comparable",
                "CustomStringConvertible",
                "Error",
                "Sendable",
            }
        ),
        color_hex="#F05138",
    ),
    LanguageSpec(
        tag="scala",
        display_name="Scala",
        extensions=frozenset({".scala"}),
        grammar_package="tree_sitter_scala",
        scm_file="scala.scm",
        heritage_node_types=frozenset(
            {"class_definition", "trait_definition", "object_definition"}
        ),
        manifest_files=("build.sbt",),
        builtin_calls=frozenset(
            {
                "println",
                "print",
                "require",
                "assert",
                "Some",
                "None",
                "Left",
                "Right",
                "Nil",
                "List",
                "Map",
                "Set",
                "Vector",
                "Array",
            }
        ),
        builtin_parents=frozenset(
            {
                "Any",
                "AnyRef",
                "AnyVal",
                "Product",
                "Serializable",
                "Throwable",
                "Exception",
                "RuntimeException",
                "Ordered",
                "Ordering",
            }
        ),
        color_hex="#DC322F",
    ),
    # -----------------------------------------------------------------
    # Config / data / markup languages (passthrough — no AST)
    # -----------------------------------------------------------------
    LanguageSpec(
        tag="shell",
        display_name="Shell",
        extensions=frozenset({".sh", ".bash", ".zsh"}),
        is_infra=True,
        is_passthrough=True,
        shebang_tokens=("bash", " sh"),
        color_hex="#89E051",
    ),
    LanguageSpec(
        tag="yaml",
        display_name="YAML",
        extensions=frozenset({".yaml", ".yml"}),
        is_code=False,
        is_passthrough=True,
        color_hex="#CB171E",
    ),
    LanguageSpec(
        tag="json",
        display_name="JSON",
        extensions=frozenset({".json"}),
        is_code=False,
        is_passthrough=True,
        color_hex="#292929",
    ),
    LanguageSpec(
        tag="toml",
        display_name="TOML",
        extensions=frozenset({".toml"}),
        is_code=False,
        is_passthrough=True,
        color_hex="#9C4221",
    ),
    LanguageSpec(
        tag="proto",
        display_name="Protocol Buffers",
        extensions=frozenset({".proto"}),
        is_code=False,
        is_passthrough=True,
        is_api_contract=True,
    ),
    LanguageSpec(
        tag="graphql",
        display_name="GraphQL",
        extensions=frozenset({".graphql", ".gql"}),
        is_code=False,
        is_passthrough=True,
        is_api_contract=True,
    ),
    LanguageSpec(
        tag="terraform",
        display_name="Terraform",
        extensions=frozenset({".tf", ".hcl"}),
        is_infra=True,
        is_passthrough=True,
        color_hex="#5C4EE5",
    ),
    LanguageSpec(
        tag="dockerfile",
        display_name="Dockerfile",
        special_filenames=frozenset({"Dockerfile", "dockerfile"}),
        is_infra=True,
        is_passthrough=True,
        color_hex="#384D54",
    ),
    LanguageSpec(
        tag="makefile",
        display_name="Makefile",
        special_filenames=frozenset({"Makefile", "makefile", "GNUmakefile"}),
        is_infra=True,
        is_passthrough=True,
        color_hex="#427819",
    ),
    LanguageSpec(
        tag="markdown",
        display_name="Markdown",
        extensions=frozenset({".md", ".mdx"}),
        is_code=False,
        is_passthrough=True,
    ),
    LanguageSpec(
        tag="sql",
        display_name="SQL",
        extensions=frozenset({".sql"}),
        is_code=False,
        is_passthrough=True,
    ),
    LanguageSpec(
        tag="openapi",
        display_name="OpenAPI",
        is_code=False,
        is_passthrough=True,
        is_api_contract=True,
    ),
    # -----------------------------------------------------------------
    # Extra languages — git blame coverage only (passthrough + is_code)
    # These exist so git_indexer tracks their history even though
    # tree-sitter grammars are not installed.
    # -----------------------------------------------------------------
    LanguageSpec(
        tag="objectivec",
        display_name="Objective-C",
        extensions=frozenset({".m", ".mm"}),
        is_passthrough=True,
    ),
    LanguageSpec(
        tag="elixir",
        display_name="Elixir",
        extensions=frozenset({".ex", ".exs"}),
        is_passthrough=True,
    ),
    LanguageSpec(
        tag="erlang",
        display_name="Erlang",
        extensions=frozenset({".erl", ".hrl"}),
        is_passthrough=True,
    ),
    LanguageSpec(
        tag="luau",
        display_name="Luau",
        # Rojo treats both .lua and .luau as Luau modules.  Luau's grammar is
        # a superset of Lua 5.1, so vanilla Lua files parse cleanly too.
        extensions=frozenset({".lua", ".luau"}),
        grammar_package="tree_sitter_luau",
        scm_file="luau.scm",
        heritage_node_types=frozenset(),
        entry_point_patterns=("init.luau", "init.lua"),
        manifest_files=("default.project.json", "wally.toml", ".rojo.json"),
        blocked_dirs=("Packages", "ServerPackages", "DevPackages"),
        builtin_calls=frozenset(
            {
                "print",
                "warn",
                "error",
                "assert",
                "pcall",
                "xpcall",
                "select",
                "type",
                "typeof",
                "tonumber",
                "tostring",
                "ipairs",
                "pairs",
                "next",
                "rawget",
                "rawset",
                "rawequal",
                "rawlen",
                "setmetatable",
                "getmetatable",
                "unpack",
                "require",
            }
        ),
        color_hex="#00A2FF",
    ),
    LanguageSpec(
        tag="r",
        display_name="R",
        extensions=frozenset({".r"}),
        is_passthrough=True,
    ),
    LanguageSpec(
        tag="dart",
        display_name="Dart",
        extensions=frozenset({".dart"}),
        is_passthrough=True,
    ),
    LanguageSpec(
        tag="zig",
        display_name="Zig",
        extensions=frozenset({".zig"}),
        is_passthrough=True,
    ),
    LanguageSpec(
        tag="julia",
        display_name="Julia",
        extensions=frozenset({".jl"}),
        is_passthrough=True,
    ),
    LanguageSpec(
        tag="clojure",
        display_name="Clojure",
        extensions=frozenset({".clj", ".cljs", ".cljc"}),
        is_passthrough=True,
    ),
    LanguageSpec(
        tag="elm",
        display_name="Elm",
        extensions=frozenset({".elm"}),
        is_passthrough=True,
    ),
    LanguageSpec(
        tag="haskell",
        display_name="Haskell",
        extensions=frozenset({".hs", ".lhs"}),
        is_passthrough=True,
    ),
    LanguageSpec(
        tag="ocaml",
        display_name="OCaml",
        extensions=frozenset({".ml", ".mli"}),
        is_passthrough=True,
    ),
    LanguageSpec(
        tag="fsharp",
        display_name="F#",
        extensions=frozenset({".fs", ".fsi", ".fsx"}),
        is_passthrough=True,
    ),
    LanguageSpec(
        tag="crystal",
        display_name="Crystal",
        extensions=frozenset({".cr"}),
        is_passthrough=True,
    ),
    LanguageSpec(
        tag="nim",
        display_name="Nim",
        extensions=frozenset({".nim"}),
        is_passthrough=True,
    ),
    LanguageSpec(
        tag="dlang",
        display_name="D",
        extensions=frozenset({".d"}),
        is_passthrough=True,
    ),
    # Sentinel for unclassified files
    LanguageSpec(
        tag="unknown",
        display_name="Unknown",
        is_code=False,
        is_passthrough=True,
    ),
)


# =========================================================================
# LanguageRegistry
# =========================================================================


class LanguageRegistry:
    """Central registry.  All language-specific lookups go through here.

    Instantiated once at module level as ``REGISTRY``.  The registry is
    immutable after construction — all data comes from ``_SPECS``.
    """

    __slots__ = ("_ext_map", "_filename_map", "_specs")

    def __init__(self, specs: tuple[LanguageSpec, ...] = _SPECS) -> None:
        self._specs: dict[str, LanguageSpec] = {s.tag: s for s in specs}

        # Build extension → tag map (first spec wins if extensions overlap)
        self._ext_map: dict[str, str] = {}
        for spec in specs:
            for ext in spec.extensions:
                if ext not in self._ext_map:
                    self._ext_map[ext] = spec.tag

        # Build special filename → tag map
        self._filename_map: dict[str, str] = {}
        for spec in specs:
            for fn in spec.special_filenames:
                if fn not in self._filename_map:
                    self._filename_map[fn] = spec.tag

    # -- Single-spec lookups ---------------------------------------------

    def get(self, tag: str) -> LanguageSpec | None:
        """Return the spec for a language tag, or None."""
        return self._specs.get(tag)

    def from_extension(self, ext: str) -> str:
        """Return the language tag for a file extension, or ``'unknown'``."""
        return self._ext_map.get(ext, "unknown")

    def from_filename(self, name: str) -> str | None:
        """Return the language tag for a special filename, or None."""
        return self._filename_map.get(name)

    # -- Aggregated lookups ----------------------------------------------

    def all_extensions(self) -> dict[str, str]:
        """Return ``{ext: tag}`` for all registered extensions."""
        return dict(self._ext_map)

    def all_special_filenames(self) -> dict[str, str]:
        """Return ``{filename: tag}`` for all special filenames."""
        return dict(self._filename_map)

    def all_code_extensions(self) -> frozenset[str]:
        """Return extensions for all ``is_code=True`` languages."""
        return frozenset(
            ext for spec in self._specs.values() if spec.is_code for ext in spec.extensions
        )

    def code_languages(self) -> frozenset[str]:
        """Return tags for code languages (not config/markup/data)."""
        return frozenset(s.tag for s in self._specs.values() if s.is_code and not s.is_passthrough)

    def config_languages(self) -> frozenset[str]:
        """Return tags for non-code languages (config/markup/data)."""
        return frozenset(s.tag for s in self._specs.values() if not s.is_code)

    def passthrough_languages(self) -> frozenset[str]:
        """Return tags for languages with no AST parser."""
        return frozenset(s.tag for s in self._specs.values() if s.is_passthrough)

    def infra_languages(self) -> frozenset[str]:
        """Return tags for infrastructure languages."""
        return frozenset(s.tag for s in self._specs.values() if s.is_infra)

    def entry_point_names(self) -> frozenset[str]:
        """Return the union of all entry-point filename patterns."""
        return frozenset(p for s in self._specs.values() for p in s.entry_point_patterns)

    def manifest_filenames(self) -> frozenset[str]:
        """Return the union of all manifest filenames."""
        return frozenset(f for s in self._specs.values() for f in s.manifest_files)

    def blocked_dirs(self) -> frozenset[str]:
        """Return the union of all per-language blocked directories."""
        return frozenset(d for s in self._specs.values() for d in s.blocked_dirs)

    def generated_suffixes(self) -> frozenset[str]:
        """Return the union of all generated-file suffixes."""
        return frozenset(sf for s in self._specs.values() for sf in s.generated_suffixes)

    def extensions_for(self, tags: Iterable[str]) -> frozenset[str]:
        """Return extensions for a specific set of language tags."""
        tag_set = set(tags)
        return frozenset(
            ext for spec in self._specs.values() if spec.tag in tag_set for ext in spec.extensions
        )

    def all_specs(self) -> list[LanguageSpec]:
        """Return all registered specs."""
        return list(self._specs.values())


# Module-level singleton
REGISTRY = LanguageRegistry()
