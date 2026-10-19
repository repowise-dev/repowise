"""Symbol-kind and symbol-name rules the export and internal passes share."""

from __future__ import annotations

from collections.abc import Set as AbstractSet

from .constants import _FRAMEWORK_INNER_CLASS_NAMES

# Kinds no language imports by name: always reached through an enclosing
# class or namespace (C# auto-properties land in the graph as ``variable``).
_UNIVERSAL_NON_IMPORTABLE: frozenset[str] = frozenset(
    {
        "method",
        "variable",
        "field",
        "property",
        "enum_member",
        "constant",
        "type_alias",
        "namespace",
        "module",
        "impl",
    }
)

# Kinds skipped where the graph cannot yet see interface use (constructor
# injection without ``via=type_use`` edges). Drop a language once it emits them.
_LANGUAGE_NON_IMPORTABLE: dict[str, frozenset[str]] = {
    "java": frozenset({"interface"}),
    "kotlin": frozenset({"interface"}),
    "scala": frozenset({"interface"}),
}


# Kinds allowed as top-level imports in TS/JS (top-level `export const` literals/objects)
_TS_JS_IMPORTABLE_KINDS: frozenset[str] = frozenset({"constant", "variable"})

# Single-file-component languages: the whole file is one component, so their
# exports behave unlike an ordinary module's; see _non_importable_kinds.
_SFC_LANGUAGES: frozenset[str] = frozenset({"svelte", "vue", "astro"})

# Every kind an SFC component prop can take, plus "class" for the synthetic
# component symbol itself; see _non_importable_kinds.
_SFC_NON_IMPORTABLE_KINDS: frozenset[str] = frozenset(
    {"constant", "variable", "function", "class", "interface", "type_alias"}
)


def _non_importable_kinds(language: str) -> frozenset[str]:
    """Per-language set of symbol kinds excluded from unused-export passes.

    An SFC's exports are its props, set by the parent as markup attributes,
    and the component itself is reached as a whole module (a tag or a
    route-level ``import()``), so neither carries a symbol-level edge.
    Ceiling: the genuinely dead named exports of an SFC module script are
    missed too; telling them from props needs attribute-to-prop binding.
    """
    if language in _SFC_LANGUAGES:
        return _UNIVERSAL_NON_IMPORTABLE | _SFC_NON_IMPORTABLE_KINDS

    extra = _LANGUAGE_NON_IMPORTABLE.get(language)
    if extra is None:
        if language in ("typescript", "javascript"):
            return _UNIVERSAL_NON_IMPORTABLE - _TS_JS_IMPORTABLE_KINDS
        return _UNIVERSAL_NON_IMPORTABLE
    return _UNIVERSAL_NON_IMPORTABLE | extra


# The universal set plus ``interface``; prefer ``_non_importable_kinds(language)``.
_NON_IMPORTABLE_SYMBOL_KINDS: frozenset[str] = _UNIVERSAL_NON_IMPORTABLE | frozenset({"interface"})

# Type kinds that are never call targets, so "no callers" says nothing about
# them in the unused-internal pass; the unused-export pass still judges them.
_UNCALLABLE_TYPE_KINDS: frozenset[str] = frozenset(
    {
        "struct",
        "interface",
        "enum",
        "type_alias",
        "impl",
    }
)


#: Kinds that declare a type, which Java and C# use from their own package or
#: namespace without an import.
_TYPE_DECLARATION_KINDS: frozenset[str] = frozenset(
    {"class", "interface", "struct", "enum", "record", "trait", "object"}
)

#: C/C++ kinds a type forward declaration (``class Env;``) can carry.
_CPP_TYPE_DECLARATION_KINDS: frozenset[str] = frozenset(
    {
        "class",
        "struct",
        "enum",
    }
)

# Symbol names that are language-runtime entry points or compiler-implicit
# anchors: never invoked by user-authored callers, never dead.
_ENTRY_POINT_SYMBOL_NAMES: frozenset[str] = frozenset(
    {
        "Main",  # C#, Java, Kotlin, Go, Rust, Swift, Scala
        "main",  # most others
        # ---- Go runtime / test conventions ------------------------------
        # ``init`` runs when the package is linked; ``go test`` calls ``TestMain``.
        "init",
        "TestMain",
        "MauiProgram",  # .NET MAUI
        "Program",  # C# top-level / classic console
        "Startup",  # ASP.NET Core legacy
        "__module__",  # synthetic per-file module anchor
        "_start",  # C runtime
        # ---- Python WSGI / ASGI / app-factory conventions ---------------
        # Loaded by an app server from a dotted path (``module:create_app``).
        "create_app",
        "make_app",
        "create_application",
        "make_application",
        "application",
        "asgi_app",
        "wsgi_app",
        "asgi_application",
        "wsgi_application",
        "get_asgi_application",
        "get_wsgi_application",
        # ---- Windows DLL / COM entry points -----------------------------
        # Invoked by the Windows loader or COM runtime.
        "DllMain",
        "DllGetClassObject",
        "DllCanUnloadNow",
        "DllRegisterServer",
        "DllUnregisterServer",
        "DllGetActivationFactory",
        "DllInstall",  # legacy MSI custom-action entry
        # ---- Win32 GUI / console entry points ---------------------------
        "wWinMain",  # Unicode WinMain
        "WinMain",  # ANSI WinMain
        "wmain",  # Unicode console main
        "ServiceMain",  # Win32 service entry
        # Screen savers: scrnsave.lib calls these three by name.
        "ScreenSaverProc",
        "ScreenSaverConfigureDialog",
        "RegisterDialogClasses",
        # ---- libFuzzer / Honggfuzz / AFL fuzz harness entries ------------
        # The fuzzer driver invokes these by name via dlsym.
        "LLVMFuzzerTestOneInput",
        "LLVMFuzzerInitialize",
        # ---- Windows hook / ETW callbacks invoked by macros / runtime ----
        "LowLevelKeyboardProc",
        "LowLevelMouseProc",
        "RegisterProvider",  # ETW provider registration (macro-invoked)
        # ---- MSTest unit-test macro --------------------------------------
        # ``TEST_METHOD(Name)`` is captured under the macro name; the runner
        # finds it by attribute reflection.
        "TEST_METHOD",
        "TEST_CLASS",
        "TEST_METHOD_INITIALIZE",
        "TEST_METHOD_CLEANUP",
        "TEST_CLASS_INITIALIZE",
        "TEST_CLASS_CLEANUP",
        "BEGIN_TEST_METHOD_PROPERTIES",
        "END_TEST_METHOD_PROPERTIES",
        # ---- Next.js (app + pages router) convention exports -------------
        # Route exports outside the convention globs. Distinctive names only:
        # common ones (``load``, ``config``, ``metadata``) are protected by the
        # file globs in :data:`_NEVER_FLAG_PATTERNS` instead.
        "generateStaticParams",
        "generateMetadata",
        "generateViewport",
        "generateImageMetadata",
        "generateSitemaps",
        "dynamicParams",
        "fetchCache",
        "preferredRegion",
        "maxDuration",
        "getStaticProps",
        "getStaticPaths",
        "getServerSideProps",
        "getInitialProps",
        "reportWebVitals",
        # ---- Remix route module exports (distinctive names only) ---------
        "shouldRevalidate",
        "ErrorBoundary",
        "CatchBoundary",
        "HydrateFallback",
        "clientLoader",
        "clientAction",
        # ---- SvelteKit page/layout module exports (distinctive names) ----
        "trailingSlash",
        # ---- JVM (Java + Kotlin) runtime / serialization / contract anchors ----
        # Resolved through reflection, serialization or generated code.
        "serialVersionUID",
        "readObject",
        "writeObject",
        "readObjectNoData",
        "readResolve",
        "writeReplace",
        "canEqual",  # Lombok-equivalent generated method
        "INSTANCE",  # Kotlin object singleton field
        "Companion",  # Kotlin companion-object accessor
    }
)


# Compiler intrinsics C/C++ libraries redefine as a fallback; their only
# callers are ``#if`` directives, which the graph cannot observe.
_CPP_BUILTIN_MACROS: frozenset[str] = frozenset(
    {
        "__has_include",
        "__has_include_next",
        "__has_feature",
        "__has_extension",
        "__has_attribute",
        "__has_cpp_attribute",
        "__has_c_attribute",
        "__has_declspec_attribute",
        "__has_builtin",
        "__has_warning",
        "__builtin_expect",
        "__builtin_unreachable",
        "__builtin_assume",
        "__builtin_constant_p",
        "__is_identifier",
        "__FILE__",
        "__LINE__",
        "__DATE__",
        "__TIME__",
        "__func__",
        "__FUNCTION__",
        "__PRETTY_FUNCTION__",
    }
)


def _is_compiler_invoked(sym: dict, sym_name: str) -> bool:
    """Symbols the compiler or linker calls, which no graph edge can show."""
    # Fallback definitions of compiler intrinsics (``__has_include``) are only
    # ever used from preprocessor directives.
    if sym.get("language") in ("cpp", "c") and sym_name in _CPP_BUILTIN_MACROS:
        return True
    # Rust proc-macro entry points are invoked by the compiler.
    if sym.get("language") == "rust":
        decorators = sym.get("decorators") or []
        if any(d.startswith("proc_macro") for d in decorators):
            return True
    # Explicit export markers (``dllexport``, ``visibility("default")``) mean
    # callers outside this binary.
    return bool(sym.get("is_exported_symbol"))


def _is_declaration_only(sym: dict) -> bool:
    """C/C++ declarations that are not a deletable unit of their own."""
    # A prototype whose definition was found: the definition carries the
    # finding. An unmatched prototype is still reported.
    if sym.get("is_declaration") and sym.get("defined_by"):
        return True
    # A type forward declaration (``class Env;``) exists so its own file can
    # name the type, which makes that file its user.
    return bool(
        sym.get("is_declaration")
        and sym.get("language") in ("cpp", "c")
        and sym.get("kind") in _CPP_TYPE_DECLARATION_KINDS
    )


def _is_public_top_level(sym: dict, kinds: AbstractSet[str] | None) -> bool:
    if sym.get("visibility") != "public" or sym.get("parent_name"):
        return False
    return kinds is None or sym.get("kind") in kinds


def _nested_class_used(node_data: dict, sym: dict) -> bool:
    """Whether a nested class is one a framework reads or its module names.

    ``ingestion/python_local_refs.py`` records a nested class its module reaches
    (``Outer.Inner``, or ``Inner`` inside ``Outer``) under its ``Outer.Inner``
    name, since a bare inner name like ``ErrorModel`` repeats across classes.
    """
    parent = sym.get("parent_name")
    if not parent:
        return False
    name = sym.get("name", "")
    if name in _FRAMEWORK_INNER_CLASS_NAMES.get(sym.get("language", ""), ()):
        return True
    return f"{parent}.{name}" in (node_data.get("local_refs") or ())
