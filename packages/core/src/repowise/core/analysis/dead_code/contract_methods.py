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
* **JVM / C++ / Godot**: the ``Object`` and STL contracts, and the Godot
  engine callbacks (``_ready``, ``_process``, …) the engine invokes on
  every node it owns.

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


# Method / function names reserved by the C++ language, the STL
# customization-point protocol, the coroutine machinery, and standard
# library traits. Each is dispatched by the compiler, the standard
# library, or a hidden friend lookup — never via a static call edge the
# graph can observe. A user class overriding ``operator==`` or
# providing ``begin()`` / ``end()`` for range-for support is live even
# when the explicit call sites read as ``std::sort(v.begin(), v.end())``
# in another TU.
#
# ``<ctor>`` / ``<dtor>`` are sentinel placeholders — the analyzer
# matches constructors and destructors by symbol *kind* rather than by
# name, because the parser emits the bare class name for constructors
# and ``~ClassName`` for destructors.
_CPP_CONTRACT_METHOD_NAMES: frozenset[str] = frozenset({
    # ---- Comparison & arithmetic operator overloads -----------------
    "operator=",
    "operator==", "operator!=",
    "operator<", "operator<=", "operator>", "operator>=", "operator<=>",
    "operator+", "operator-", "operator*", "operator/", "operator%",
    "operator&", "operator|", "operator^", "operator~",
    "operator!", "operator&&", "operator||",
    "operator++", "operator--",
    "operator+=", "operator-=", "operator*=", "operator/=", "operator%=",
    "operator&=", "operator|=", "operator^=",
    "operator<<", "operator>>", "operator<<=", "operator>>=",
    "operator,",
    # ---- Indexing / call / member-access operators ------------------
    "operator[]",
    "operator()",
    "operator->",
    "operator->*",
    "operator*",                 # also dereference; deduped by set  # noqa: B033
    # ---- Allocation operators (overloaded new/delete) ---------------
    "operator new",
    "operator new[]",
    "operator delete",
    "operator delete[]",
    # ---- Conversion operators (typed by the parser as operator T) ----
    "operator bool",
    "operator int",
    "operator double",
    "operator float",
    # ---- STL container / iteration customization points -------------
    "begin", "end",
    "cbegin", "cend",
    "rbegin", "rend",
    "crbegin", "crend",
    "size", "max_size", "empty",
    "data", "swap",
    "hash_value",
    "to_string",
    # ---- Hash specialization customization (called via std::hash) ----
    # ``operator()`` already covered above.
    # ---- Coroutine customization points -----------------------------
    "await_ready", "await_suspend", "await_resume",
    "promise_type",
    "get_return_object",
    "initial_suspend", "final_suspend",
    "return_void", "return_value",
    "unhandled_exception",
    "yield_value",
    # ---- std::format / std::print customization ---------------------
    "format", "format_to", "parse",
    # ---- std::error_code / std::error_category interface ------------
    "message", "name", "default_error_condition",
    # ---- Standard library tag / trait override methods --------------
    "value_type", "key_type", "mapped_type",
    "iterator", "const_iterator",
    "reference", "const_reference",
})


_CPP_LANGUAGES: frozenset[str] = frozenset({"cpp", "c"})


# Godot engine callbacks. The engine invokes these on every node it owns:
# ``_ready`` when the node enters the scene tree, ``_process`` once a frame,
# ``_input`` on every event. So a script that does nothing but override them
# is the *normal* shape of a Godot script and has no static caller anywhere.
# Without this, every ``.gd`` file in a Godot project reads as a file full of
# uncalled private functions.
#
# All of them are leading-underscore, which GDScript's visibility convention
# (shared with Python) reads as private, which is exactly why they land in
# the uncalled-private pass rather than the unused-export one.
#
# Godot documents ~200 virtuals across its class hierarchy. Enumerating them
# all would be arbitrary and stale within a release, so this covers the ones
# a project actually overrides: the ``Node`` / ``CanvasItem`` lifecycle, the
# ``Object`` property protocol, and the ``EditorPlugin`` interface an
# ``addons/`` plugin must implement. The ceiling is the mirror image of the
# gap: a project method that happens to share a name with a Godot virtual is
# silently exempted. Contained to GDScript, and every name here starts with
# ``_``, which a project author uses for a helper it does not expect anyone
# else to call either.
_GODOT_CALLBACK_NAMES: frozenset[str] = frozenset({
    # ---- Object / RefCounted ----------------------------------------
    "_init",
    "_notification",
    "_to_string",
    # Property protocol: the engine calls these when the inspector or a
    # script reads/writes a property that does not exist as a real field.
    "_get",
    "_set",
    "_get_property_list",
    "_validate_property",
    "_property_can_revert",
    "_property_get_revert",
    # ---- Node lifecycle ---------------------------------------------
    "_ready",
    "_enter_tree",
    "_exit_tree",
    "_process",
    "_physics_process",
    "_get_configuration_warnings",
    # ---- Input ------------------------------------------------------
    "_input",
    "_unhandled_input",
    "_unhandled_key_input",
    "_shortcut_input",
    "_gui_input",
    "_input_event",
    # ---- CanvasItem / Control drawing and hit-testing ----------------
    "_draw",
    "_has_point",
    "_get_minimum_size",
    "_make_custom_tooltip",
    "_structured_text_parser",
    # ---- Control drag and drop --------------------------------------
    "_get_drag_data",
    "_can_drop_data",
    "_drop_data",
    # ---- GDScript 3 spellings ---------------------------------------
    # Godot 3 named several of these virtuals without a leading underscore and
    # singularised the configuration-warning hook. Both dialects parse (see
    # LANGUAGE_SUPPORT.md), so both spellings belong here. The
    # underscore-free ones read as *public*, which puts them in the
    # unused-export pass rather than the uncalled-private one.
    "get_drag_data",
    "can_drop_data",
    "drop_data",
    "make_custom_tooltip",
    "_get_configuration_warning",
    # ---- Physics bodies ---------------------------------------------
    "_integrate_forces",
    # ---- EditorPlugin: what an addons/ plugin must implement ---------
    "_enable_plugin",
    "_disable_plugin",
    "_get_plugin_name",
    "_get_plugin_icon",
    "_has_main_screen",
    "_make_visible",
    "_handles",
    "_edit",
    "_apply_changes",
    "_save_external_data",
    "_get_window_layout",
    "_set_window_layout",
    "_build",
})


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
    # The parser emits constructors as ``kind="method"`` with the bare
    # class name, and destructors as ``kind="method"`` with a leading
    # ``~``. Either way, the language runtime dispatches them
    # (construction site / object teardown / RAII unwind), not a static
    # caller. Treat both as contract methods for C/C++.
    if sym_kind in ("constructor", "destructor") and language in _CPP_LANGUAGES:
        return True
    if sym_kind not in ("method", "function"):
        return False
    if language in _COM_LANGUAGES and sym_name in _COM_CONTRACT_METHOD_NAMES:
        return True
    if language in _JVM_LANGUAGES and sym_name in _JVM_CONTRACT_METHOD_NAMES:
        return True
    if language == "gdscript" and sym_name in _GODOT_CALLBACK_NAMES:
        return True
    if language in _CPP_LANGUAGES:
        if sym_name in _CPP_CONTRACT_METHOD_NAMES:
            return True
        # Destructor names land as ``~ClassName`` in the symbol name —
        # match by prefix so we don't have to enumerate every class.
        if sym_name.startswith("~"):
            return True
        # Generic conversion operator: ``operator Foo`` where ``Foo`` is
        # a user type. The prefix is sufficient evidence.
        if sym_name.startswith("operator "):
            return True
    return False
