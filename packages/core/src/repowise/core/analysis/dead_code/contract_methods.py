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
    if language not in _COM_LANGUAGES:
        return False
    return sym_name in _COM_CONTRACT_METHOD_NAMES
