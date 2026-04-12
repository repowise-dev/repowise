"""Per-language constants for call/heritage resolution filtering.

Centralises all language-specific data tables (builtin functions, builtin
parent types, test path patterns) in one place so they can be adjusted
without touching resolution or extraction logic.

Adding a new language:
  1. Add its builtins to ``BUILTIN_CALLS`` (functions the language provides
     globally that should NOT create graph edges).
  2. Add its builtin parent types to ``BUILTIN_PARENTS`` (base classes /
     traits / interfaces provided by the language runtime that should NOT
     create heritage edges).
  3. That's it — the resolver and parser import these dicts automatically.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Builtin function calls — filtered during call extraction (parser.py)
#
# These are names that appear as function calls in source code but refer to
# language-provided builtins, not user-defined symbols.  Extracting them
# pollutes the call graph because Tier 3 global resolution can match them
# to unrelated user symbols with the same name in other files/languages.
# ---------------------------------------------------------------------------

BUILTIN_CALLS: dict[str, frozenset[str]] = {
    "python": frozenset({
        # builtins module — https://docs.python.org/3/library/functions.html
        "abs", "aiter", "all", "anext", "any", "ascii",
        "bin", "bool", "breakpoint", "bytearray", "bytes",
        "callable", "chr", "classmethod", "compile", "complex",
        "delattr", "dict", "dir", "divmod",
        "enumerate", "eval", "exec",
        "filter", "float", "format", "frozenset",
        "getattr", "globals",
        "hasattr", "hash", "help", "hex",
        "id", "input", "int", "isinstance", "issubclass", "iter",
        "len", "list", "locals",
        "map", "max", "memoryview", "min",
        "next",
        "object", "oct", "open", "ord",
        "pow", "print", "property",
        "range", "repr", "reversed", "round",
        "set", "setattr", "slice", "sorted", "staticmethod", "str", "sum", "super",
        "tuple", "type",
        "vars",
        "zip",
        "__import__",
    }),
    "typescript": frozenset({
        # Global functions & constructors
        "parseInt", "parseFloat", "isNaN", "isFinite",
        "decodeURI", "decodeURIComponent", "encodeURI", "encodeURIComponent",
        "setTimeout", "setInterval", "clearTimeout", "clearInterval",
        "fetch", "require", "eval", "atob", "btoa",
        # Global object namespaces (not user-callable symbols)
        "JSON", "Math", "console", "Reflect", "Proxy",
        # Builtin constructors — user code rarely shadows these
        "Object", "Array", "String", "Number", "Boolean",
        "Date", "RegExp", "Promise", "Set", "Map", "WeakMap", "WeakSet",
        "Symbol", "ArrayBuffer", "DataView", "Uint8Array",
        "Error", "TypeError", "RangeError", "SyntaxError", "ReferenceError",
        "Int8Array", "Int16Array", "Int32Array", "Float32Array", "Float64Array",
    }),
    # JavaScript shares the TypeScript set
    "javascript": None,
    "java": frozenset({
        # java.lang auto-imported types used as static calls
        "System", "Objects", "Arrays", "Collections", "Math",
        "Integer", "Long", "Double", "Float", "Boolean", "Character",
        "Byte", "Short",
        "String", "Object", "Class", "Thread",
        "Throwable", "Exception", "RuntimeException", "Error",
        "StringBuilder", "StringBuffer",
    }),
    "go": frozenset({
        # Go builtins — https://pkg.go.dev/builtin
        "make", "len", "cap", "new", "append", "copy", "close",
        "delete", "complex", "real", "imag",
        "panic", "recover",
        "print", "println",
    }),
    "rust": frozenset({
        # Macros and builtins that tree-sitter extracts as calls
        "println", "eprintln", "print", "eprint",
        "format", "format_args",
        "vec", "panic", "todo", "unimplemented", "unreachable",
        "assert", "assert_eq", "assert_ne",
        "debug_assert", "debug_assert_eq", "debug_assert_ne",
        "cfg", "include", "include_str", "include_bytes",
        "env", "option_env", "concat", "stringify", "line", "column", "file",
        "write", "writeln",
    }),
    "kotlin": frozenset({
        "println", "print", "readLine",
        "arrayOf", "listOf", "mutableListOf", "setOf", "mutableSetOf",
        "mapOf", "mutableMapOf", "hashMapOf",
        "lazy", "require", "check", "error",
        "TODO", "run", "let", "also", "apply", "with",
    }),
    "ruby": frozenset({
        "puts", "print", "p", "pp", "raise", "fail",
        "require", "require_relative", "include", "extend", "prepend",
        "attr_reader", "attr_writer", "attr_accessor",
        "lambda", "proc",
    }),
    "cpp": frozenset({
        # C++ stdlib commonly-extracted calls
        "printf", "scanf", "fprintf", "sprintf", "snprintf",
        "malloc", "calloc", "realloc", "free",
        "sizeof", "alignof", "typeid", "decltype",
        "static_cast", "dynamic_cast", "const_cast", "reinterpret_cast",
        "move", "forward", "make_shared", "make_unique", "make_pair",
        "cout", "cerr", "endl",
    }),
    "csharp": frozenset({
        "Console", "Math", "Convert",
        "String", "Object", "Array",
        "GC", "Environment", "Activator",
        "Task", "Interlocked",
        "nameof", "typeof", "sizeof", "default",
    }),
    "c": frozenset({
        "printf", "scanf", "fprintf", "sprintf", "snprintf",
        "malloc", "calloc", "realloc", "free",
        "memcpy", "memset", "memmove", "memcmp",
        "strlen", "strcpy", "strncpy", "strcat", "strcmp", "strncmp",
        "sizeof", "offsetof",
        "assert", "abort", "exit",
    }),
}


# ---------------------------------------------------------------------------
# Builtin parent types — filtered during heritage extraction (parser.py)
#
# Base classes, interfaces, and traits provided by the language runtime.
# Creating heritage edges to these pollutes the graph because they are
# universally inherited and carry no architectural signal.
# ---------------------------------------------------------------------------

BUILTIN_PARENTS: dict[str, frozenset[str]] = {
    "python": frozenset({
        "object", "Exception", "BaseException",
        "type", "ABC", "ABCMeta",
        "Protocol",  # typing.Protocol
        "NamedTuple", "TypedDict",
        "Enum", "IntEnum", "Flag", "IntFlag",  # enum base classes
    }),
    "typescript": frozenset({
        "Error", "Object",
    }),
    "javascript": frozenset({
        "Error", "Object",
    }),
    "java": frozenset({
        "Object", "Throwable", "Exception", "RuntimeException", "Error",
        "Enum",
        "Serializable", "Cloneable", "Comparable", "Iterable",
        "AutoCloseable", "Closeable",
    }),
    "go": frozenset({
        "error",  # Go's error interface
    }),
    "rust": frozenset({
        # std::fmt, std::cmp, std::ops, std::marker traits
        "Error", "Display", "Debug",
        "Clone", "Copy", "Default",
        "PartialEq", "Eq", "PartialOrd", "Ord", "Hash",
        "Send", "Sync", "Sized", "Unpin",
        "Iterator", "IntoIterator",
        "From", "Into", "TryFrom", "TryInto",
        "AsRef", "AsMut", "Borrow", "BorrowMut",
        "Drop", "Deref", "DerefMut",
        "Add", "Sub", "Mul", "Div", "Rem", "Neg",
        "Fn", "FnMut", "FnOnce",
    }),
    "kotlin": frozenset({
        "Any", "Throwable", "Exception", "RuntimeException", "Error",
        "Enum",
        "Comparable", "Iterable", "Serializable",
    }),
    "cpp": frozenset({
        "exception", "runtime_error", "logic_error",
        "invalid_argument", "out_of_range", "overflow_error",
        # STL containers (not architectural parents)
        "string", "vector", "map", "set", "list", "deque",
        "unordered_map", "unordered_set",
        "shared_ptr", "unique_ptr", "weak_ptr",
    }),
    "csharp": frozenset({
        "Object", "ValueType", "Enum",
        "Exception", "SystemException", "ApplicationException",
        "IDisposable", "IEnumerable", "IEnumerator",
        "IComparable", "ICloneable", "IEquatable",
    }),
    "ruby": frozenset({
        "Object", "BasicObject",
        "Exception", "StandardError", "RuntimeError",
        "ScriptError", "LoadError", "SyntaxError",
        "Comparable", "Enumerable", "Kernel",
    }),
}


def get_builtin_calls(language: str) -> frozenset[str]:
    """Return the builtin call names for a language.

    Falls back to the ``typescript`` set for ``javascript`` when the
    javascript key is ``None``.
    """
    result = BUILTIN_CALLS.get(language)
    if result is not None:
        return result
    # javascript → typescript fallback
    if language == "javascript":
        return BUILTIN_CALLS.get("typescript", frozenset())
    return frozenset()


def get_builtin_parents(language: str) -> frozenset[str]:
    """Return the builtin parent type names for a language."""
    return BUILTIN_PARENTS.get(language, frozenset())
