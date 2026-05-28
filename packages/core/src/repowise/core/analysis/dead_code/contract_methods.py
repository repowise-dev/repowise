"""Well-known contract method names whose absence-of-callers is not evidence of death.

Some method names are reserved by language runtimes, ABI conventions,
or COM-style interface contracts. They are dispatched through vtables /
reflection / native interop — never through a static call edge the
graph can observe.

The dead-code analyzer treats a symbol matching one of these as if it
implements a contract: confidence is clamped below the safe-to-delete
threshold (≤ 0.4) so the report doesn't ship them as confident dead
code. The clamp is conservative on purpose — these are heuristic name
matches, not language-aware semantic checks.

Currently covers:

* **COM / IUnknown / IDispatch** — every COM object must expose
  ``QueryInterface``, ``AddRef``, ``Release`` (and dispatch types add
  ``GetIDsOfNames``, ``Invoke``, etc.). They never appear as static
  callers in C# / C++ COM-interop code because the runtime resolves
  the vtable slot.

Extend this list (and the matching helper) when other reserved-name
patterns surface — e.g. WinRT activation factories, .NET ``ToString``
overrides without static callers, etc.
"""

from __future__ import annotations


# Method names reserved by COM / IUnknown / IDispatch. Case-sensitive —
# Windows COM uses PascalCase universally.
_COM_CONTRACT_METHOD_NAMES: frozenset[str] = frozenset({
    # IUnknown
    "QueryInterface",
    "AddRef",
    "Release",
    # IDispatch
    "GetTypeInfoCount",
    "GetTypeInfo",
    "GetIDsOfNames",
    "Invoke",
    # IClassFactory
    "CreateInstance",
    "LockServer",
    # IMarshal (rarely user-implemented but same rationale)
    "GetUnmarshalClass",
    "GetMarshalSizeMax",
    "MarshalInterface",
    "UnmarshalInterface",
    "ReleaseMarshalData",
    "DisconnectObject",
})


# Languages where COM contract names are load-bearing. C++ / C# are the
# overwhelming majority; Rust ``windows-rs`` derivations also surface
# these names in user code via the ``#[implement]`` macro.
_COM_LANGUAGES: frozenset[str] = frozenset({"cpp", "c", "csharp", "rust"})


# Method names reserved by JVM ``Object`` / ``Serializable`` / ``Comparable`` /
# ``Cloneable`` contracts and by language-emitted synthesis (Kotlin data
# classes' ``componentN``/``copy``, enum classes' ``values``/``valueOf``,
# Lombok-generated ``canEqual``). Each is dispatched by the JVM through
# vtable, the serialization machinery, the reflective ``Object`` API, or
# language-emitted runtime helpers — never via a static call edge the
# graph can observe. A live class with no other inbound edges should not
# read as dead simply because it overrode ``equals`` and ``hashCode``.
_JVM_CONTRACT_METHOD_NAMES: frozenset[str] = frozenset({
    # java.lang.Object overrides
    "equals",
    "hashCode",
    "toString",
    "clone",
    "finalize",
    # java.lang.Comparable / Comparator
    "compareTo",
    "compare",
    # java.io.Serializable / Externalizable
    "readObject",
    "writeObject",
    "readObjectNoData",
    "readResolve",
    "writeReplace",
    "readExternal",
    "writeExternal",
    # Lombok-generated equality helper
    "canEqual",
    # Kotlin data-class synthesised members (also valid as a Java record
    # accessor pattern when the name happens to collide)
    "copy",
    # Enum-class static helpers emitted by ``javac`` / Kotlin
    "values",
    "valueOf",
    # ``componentN()`` accessors emitted by Kotlin data classes — names
    # are ``component1`` .. ``component22``. Listed individually below.
})

# Kotlin data-class ``componentN`` accessors — ``component1`` through
# ``component22`` is the practical ceiling (Kotlin stdlib's ``Tuple``
# limit). Listed explicitly so a single set lookup covers the case.
_JVM_CONTRACT_METHOD_NAMES = _JVM_CONTRACT_METHOD_NAMES | frozenset(
    f"component{i}" for i in range(1, 23)
)


_JVM_LANGUAGES: frozenset[str] = frozenset({"java", "kotlin"})


def is_contract_method(sym_name: str, sym_kind: str | None, language: str | None) -> bool:
    """Return True if *sym_name* is a reserved contract-method name in *language*.

    The check is intentionally narrow: only kind=``method`` symbols in
    a language where the name is load-bearing match. A user-defined
    free function named ``Release`` in TypeScript is left alone.
    """
    # C++ tree-sitter sometimes emits method definitions outside the
    # class body (e.g. ``STDMETHODIMP CFoo::QueryInterface(...)``) as
    # kind=function rather than method. Accept both — the name + COM
    # language combination is restrictive enough on its own.
    if sym_kind not in ("method", "function"):
        return False
    if language in _COM_LANGUAGES and sym_name in _COM_CONTRACT_METHOD_NAMES:
        return True
    if language in _JVM_LANGUAGES and sym_name in _JVM_CONTRACT_METHOD_NAMES:
        return True
    return False
