"""LanguageSpec for Elixir.

Grammar: tree-sitter-elixir (PyPI ``tree-sitter-elixir``, import name
``tree_sitter_elixir``), the elixir-lang project's own grammar.

Elixir has one node kind for everything a definition or a directive can be:
``defmodule``, ``def``, ``defp``, ``alias``, ``import``, ``use`` and every
``@attribute`` all parse as a ``call`` whose ``target`` is an ``identifier``
holding the keyword text. That single fact shapes the whole entry -- see
``queries/elixir.scm`` for the query side and ``LANGUAGE_CONFIGS["elixir"]``
for why the node type maps to a non-callable placeholder kind.

No heritage is emitted. ``use Foo`` and ``@behaviour Foo`` are the nearest
things Elixir has to inheritance, but neither is one: ``use`` injects code at
compile time and ``@behaviour`` promises a callback set, and both already
produce a module dependency through the import and type-reference captures.
An ``extends`` edge would say something about the code that is not true.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="elixir",
    display_name="Elixir",
    # ExUnit conventions: test/foo_test.exs + test/test_helper.exs.
    test_stem_suffixes=("_test",),
    test_fixture_stems=("test_helper",),
    suite_anchor_stems=("test_helper",),
    # OTP Application callback (lib/<app>/application.ex); mix.exs is a
    # manifest, not an entry.
    entry_point_patterns=("application.ex",),
    manifest_files=("mix.exs",),
    extensions=frozenset({".ex", ".exs"}),
    grammar_package="tree_sitter_elixir",
    scm_file="elixir.scm",
    # Kept "partial", not raised: the AST tier feeds the same regex-built
    # defmodule index the lightweight tier fed, so the resolver's reach did
    # not change -- only the accuracy of what reaches it.
    import_support="partial",
    # Kernel macros and special forms. Every definition keyword is here
    # because `def add(a, b)` parses as a call to `def`, and the control-flow
    # and attribute names because `if`, `case` and `@doc "..."` are calls
    # too; none of them is an edge in anyone's call graph. Ordinary Kernel
    # functions a project could plausibly redefine (``apply``, ``send``,
    # ``to_string``) are deliberately absent: this set is matched
    # receiver-blind, so a name here deletes the call site outright.
    builtin_calls=frozenset({
        "defmodule", "def", "defp", "defmacro", "defmacrop", "defguard",
        "defguardp", "defdelegate", "defstruct", "defexception",
        "defprotocol", "defimpl", "defoverridable",
        "alias", "import", "require", "use",
        "quote", "unquote", "unquote_splicing",
        "if", "unless", "case", "cond", "with", "for", "receive", "try",
        "raise", "reraise", "throw", "fn", "super",
        "is_atom", "is_binary", "is_bitstring", "is_boolean", "is_exception",
        "is_float", "is_function", "is_integer", "is_list", "is_map",
        "is_map_key", "is_nil", "is_number", "is_pid", "is_port",
        "is_reference", "is_struct", "is_tuple",
    }),
    color_hex="#6E4A7E",
)
