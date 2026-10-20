"""Unit tests for the Elixir language pipeline.

Tests parse inline byte strings so no filesystem I/O is needed. Elixir's one
hard fact drives most of what is asserted here: `defmodule`, `def`, `alias`
and `@doc` are all the same node kind (`call`), so the tests pin that a
definition never reads as a call to itself, that a keyword never reads as a
call, and that a module attribute never does either -- see
docs/architecture/language-support.md for the registration recipe.
"""

from __future__ import annotations

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info


def _ex(path: str = "lib/calc.ex") -> object:
    return _make_file_info(path, "elixir")


MODULE_SOURCE = b'''\
defmodule Calc.Core do
  @moduledoc """
  Adds numbers.
  """
  @behaviour Calc.Behaviour

  alias Calc.{Rounding, Formatting}
  alias Calc.Legacy.Adder, as: Adder
  import Enum, only: [map: 2]
  require Logger
  use GenServer

  defstruct [:total]

  @doc "Adds two numbers."
  @spec add(integer(), integer()) :: integer()
  def add(a, b), do: a + b

  defp normalise(value) when is_integer(value) do
    Logger.info("normalising")
    round_up(value)
    value |> Rounding.apply()
    %Calc.Total{value: value}
  end

  def zero, do: 0

  defmacro trace(expression), do: expression

  defguardp is_small(value) when value < 10
end
'''


class TestElixirSymbols:
    def test_module_and_function_kinds(self, parser: ASTParser) -> None:
        result = parser.parse_file(_ex(), MODULE_SOURCE)
        kinds = {(s.name, s.kind) for s in result.symbols}
        # Every one of these is the same tree-sitter node kind (`call`); the
        # kind comes from the keyword in the call's target.
        assert ("Calc.Core", "module") in kinds
        assert ("add", "method") in kinds
        assert ("normalise", "method") in kinds
        assert ("zero", "method") in kinds
        assert ("trace", "macro") in kinds
        assert ("is_small", "macro") in kinds

    def test_functions_take_the_enclosing_module_as_parent(self, parser: ASTParser) -> None:
        # A `def` nested in a `defmodule` has a `call` ancestor. If the config
        # mapped `call` to a callable kind, the callable-ancestor filter would
        # drop every function here.
        result = parser.parse_file(_ex(), MODULE_SOURCE)
        parents = {s.name: s.parent_name for s in result.symbols}
        assert parents["add"] == "Calc.Core"
        assert parents["normalise"] == "Calc.Core"
        assert parents["Calc.Core"] is None

    def test_private_keywords_set_private_visibility(self, parser: ASTParser) -> None:
        result = parser.parse_file(_ex(), MODULE_SOURCE)
        visibility = {s.name: s.visibility for s in result.symbols}
        assert visibility["normalise"] == "private"
        assert visibility["is_small"] == "private"
        assert visibility["add"] == "public"

    def test_zero_arity_and_guarded_heads_are_both_found(self, parser: ASTParser) -> None:
        # `def zero` has a bare identifier head, `defp normalise(v) when ...`
        # wraps its head in a `when` binary_operator, and `def add(a, b)` has
        # the plain nested-call head. Three shapes, one concept.
        result = parser.parse_file(_ex(), MODULE_SOURCE)
        names = {s.name for s in result.symbols}
        assert {"add", "normalise", "zero"} <= names

    def test_nested_module_is_its_own_symbol(self, parser: ASTParser) -> None:
        source = b"""\
defmodule Outer do
  defmodule Inner do
    def run, do: :ok
  end
end
"""
        result = parser.parse_file(_ex(), source)
        by_name = {s.name: s for s in result.symbols}
        assert by_name["Inner"].kind == "module"
        assert by_name["Inner"].parent_name == "Outer"
        assert by_name["run"].parent_name == "Inner"

    def test_defimpl_is_named_for_the_module_the_compiler_generates(
        self, parser: ASTParser
    ) -> None:
        # `defimpl Jason.Encoder, for: Tuple` compiles to a module called
        # `Jason.Encoder.Tuple`. Named for the protocol alone, its `def`s were
        # attributed to the enclosing module and read as unused exports of it.
        source = b"""\
defmodule Shop.Logs do
  defimpl Jason.Encoder, for: Tuple do
    def encode(value, opts), do: value
  end
end
"""
        result = parser.parse_file(_ex(), source)
        by_name = {s.name: s for s in result.symbols}
        assert by_name["Jason.Encoder.Tuple"].kind == "impl"
        assert by_name["Jason.Encoder.Tuple"].parent_name == "Shop.Logs"
        assert by_name["encode"].parent_name == "Jason.Encoder.Tuple"

    def test_two_impls_of_one_protocol_get_distinct_ids(self, parser: ASTParser) -> None:
        source = b"""\
defimpl Proto, for: Foo do
  def run(x), do: x
end

defimpl Proto, for: Bar do
  def run(x), do: x
end
"""
        result = parser.parse_file(_ex(), source)
        ids = [s.id for s in result.symbols]
        assert len(ids) == len(set(ids)), ids
        assert {"Proto.Foo", "Proto.Bar"} <= {s.name for s in result.symbols}

    def test_a_defimpl_without_for_takes_the_enclosing_module_as_its_type(
        self, parser: ASTParser
    ) -> None:
        # The `for:`-less form is only legal inside a `defmodule` and
        # implements the protocol for that module.
        source = b"""\
defmodule Selfish do
  defimpl Inspect do
    def inspect(value, opts), do: value
  end
end
"""
        result = parser.parse_file(_ex(), source)
        by_name = {s.name: s for s in result.symbols}
        assert by_name["Inspect.Selfish"].parent_name == "Selfish"
        assert by_name["inspect"].parent_name == "Inspect.Selfish"

    def test_a_def_inside_quote_is_not_a_symbol(self, parser: ASTParser) -> None:
        # A `def` in a `quote` block defines nothing in the module that writes
        # it: it is macro body, injected into whichever module later calls the
        # macro. Captured, it reads as a method of the wrong module.
        source = b"""\
defmodule Shop.Logs do
  defmacro __using__(_opts) do
    quote do
      def injected(x), do: x
    end
  end
end
"""
        result = parser.parse_file(_ex(), source)
        names = {s.name for s in result.symbols}
        assert "injected" not in names
        assert "__using__" in names

    def test_protocol_is_an_interface(self, parser: ASTParser) -> None:
        source = b"""\
defprotocol Serialisable do
  def dump(value)
end
"""
        result = parser.parse_file(_ex(), source)
        kinds = {(s.name, s.kind) for s in result.symbols}
        assert ("Serialisable", "interface") in kinds
        assert ("dump", "method") in kinds


class TestElixirImports:
    def test_directives_become_imports(self, parser: ASTParser) -> None:
        result = parser.parse_file(_ex(), MODULE_SOURCE)
        modules = [i.module_path for i in result.imports]
        assert "Calc.Legacy.Adder" in modules
        assert "Enum" in modules
        assert "Logger" in modules
        assert "GenServer" in modules

    def test_multi_alias_yields_one_import_per_member(self, parser: ASTParser) -> None:
        # `alias Calc.{Rounding, Formatting}` is one statement naming two
        # modules; dedup by raw statement text would keep only the first.
        result = parser.parse_file(_ex(), MODULE_SOURCE)
        modules = [i.module_path for i in result.imports]
        assert "Calc.Rounding" in modules
        assert "Calc.Formatting" in modules

    def test_import_binds_every_public_name(self, parser: ASTParser) -> None:
        result = parser.parse_file(_ex(), MODULE_SOURCE)
        by_module = {i.module_path: i for i in result.imports}
        assert by_module["Enum"].imported_names == ["*"]
        # alias/require/use bind the module itself, not its functions.
        assert by_module["Logger"].imported_names == []

    def test_erlang_atom_module_is_skipped(self, parser: ASTParser) -> None:
        # `:math` is an Erlang module: no Elixir file can ever resolve it.
        result = parser.parse_file(_ex(), b"defmodule A do\n  import :math\nend\n")
        assert [i.module_path for i in result.imports] == []


class TestElixirCalls:
    def test_local_remote_and_pipe_targets(self, parser: ASTParser) -> None:
        result = parser.parse_file(_ex(), MODULE_SOURCE)
        calls = {(c.receiver_name, c.target_name) for c in result.calls}
        assert (None, "round_up") in calls
        assert ("Logger", "info") in calls
        # The right side of `|>` is already a call node, so it needs no
        # pattern of its own.
        assert ("Rounding", "apply") in calls

    def test_definition_keywords_are_never_call_targets(self, parser: ASTParser) -> None:
        result = parser.parse_file(_ex(), MODULE_SOURCE)
        targets = {c.target_name for c in result.calls}
        reserved = {
            "def",
            "defp",
            "defmodule",
            "defmacro",
            "defguardp",
            "defstruct",
            "alias",
            "import",
            "require",
            "use",
        }
        assert not (targets & reserved)

    def test_a_definition_head_is_not_a_call_to_itself(self, parser: ASTParser) -> None:
        # `def add(a, b)` puts `add(a, b)` inside the arguments of a call to
        # `def`, so the head reads as a call unless it is filtered out.
        result = parser.parse_file(_ex(), MODULE_SOURCE)
        assert not [c for c in result.calls if c.target_name in ("add", "normalise", "zero")]

    def test_a_deep_union_typespec_still_leaks_nothing(self, parser: ASTParser) -> None:
        # A union return type nests one binary_operator per member, so the type
        # names sit deeper than any fixed ancestor budget. The walk is bounded
        # by the statement instead.
        source = b"""\
defmodule A do
  @spec f(integer()) ::
          {:ok, integer()} | {:error, atom()} | {:maybe, list(integer())} | {:x, map()}
  def f(x), do: x
end
"""
        result = parser.parse_file(_ex(), source)
        assert result.calls == [], result.calls

    def test_module_attributes_are_not_calls(self, parser: ASTParser) -> None:
        result = parser.parse_file(_ex(), MODULE_SOURCE)
        targets = {c.target_name for c in result.calls}
        assert not (targets & {"doc", "moduledoc", "spec", "behaviour"})

    def test_typespec_bodies_are_not_calls(self, parser: ASTParser) -> None:
        # `@spec add(integer(), integer()) :: integer()` holds three call
        # nodes and invokes nothing.
        result = parser.parse_file(_ex(), MODULE_SOURCE)
        assert not [c for c in result.calls if c.target_name == "integer"]

    def test_code_in_a_non_typespec_attribute_keeps_its_edges(self, parser: ASTParser) -> None:
        # A module attribute computed at compile time is real code.
        source = b"""\
defmodule A do
  @endpoint Application.compile_env(:app, :endpoint)
end
"""
        result = parser.parse_file(_ex(), source)
        calls = {(c.receiver_name, c.target_name) for c in result.calls}
        assert ("Application", "compile_env") in calls
        assert "endpoint" not in {c.target_name for c in result.calls}

    def test_guard_calls_resolve_as_ordinary_calls(self, parser: ASTParser) -> None:
        # A guard can be a project's own `defguard`, so the edge is kept;
        # Kernel's own guards are dropped by name through builtin_calls.
        source = b"""\
defmodule A do
  def run(x) when is_small(x), do: x
end
"""
        result = parser.parse_file(_ex(), source)
        targets = {c.target_name for c in result.calls}
        assert "is_small" in targets
        assert "is_integer" not in targets

    def test_calls_are_attributed_to_their_enclosing_function(self, parser: ASTParser) -> None:
        result = parser.parse_file(_ex(), MODULE_SOURCE)
        info = next(c for c in result.calls if c.target_name == "info")
        assert info.caller_symbol_id == "lib/calc.ex::Calc.Core::normalise"


class TestElixirTypeReferences:
    def test_struct_literal_and_behaviour_are_type_references(self, parser: ASTParser) -> None:
        result = parser.parse_file(_ex(), MODULE_SOURCE)
        names = {t.type_name for t in result.type_refs}
        assert "Calc.Total" in names
        assert "Calc.Behaviour" in names

    def test_a_type_position_never_becomes_a_call(self, parser: ASTParser) -> None:
        result = parser.parse_file(_ex(), MODULE_SOURCE)
        targets = {c.target_name for c in result.calls}
        assert "Calc.Total" not in targets
        assert "Calc.Behaviour" not in targets


class TestElixirDocstrings:
    def test_moduledoc_becomes_the_module_docstring(self, parser: ASTParser) -> None:
        result = parser.parse_file(_ex(), MODULE_SOURCE)
        assert result.docstring == "Adds numbers."

    def test_doc_attaches_to_the_following_definition(self, parser: ASTParser) -> None:
        # @doc and the def it documents are siblings with no AST link, and
        # @spec sits between them here.
        result = parser.parse_file(_ex(), MODULE_SOURCE)
        add = next(s for s in result.symbols if s.name == "add")
        assert add.docstring == "Adds two numbers."

    def test_an_undocumented_function_has_no_docstring(self, parser: ASTParser) -> None:
        result = parser.parse_file(_ex(), MODULE_SOURCE)
        zero = next(s for s in result.symbols if s.name == "zero")
        assert zero.docstring is None


class TestElixirEncoding:
    def test_non_ascii_source_keeps_byte_accurate_names(self, parser: ASTParser) -> None:
        # Function names, docs and string arguments are all multi-byte here,
        # so any byte-offset slicing of the decoded source misaligns. The
        # module alias stays ASCII on purpose: tree-sitter-elixir's `alias`
        # rule accepts ASCII only, which is the grammar's limit, not ours.
        source = """\
defmodule Greeter do
  @doc "Grüße, Welt"
  def grüßen(name), do: "Hallo #{name}"

  def caller, do: grüßen("Ünal")
end
""".encode()
        result = parser.parse_file(_ex(), source)
        names = {s.name for s in result.symbols}
        assert "grüßen" in names
        greeter = next(s for s in result.symbols if s.name == "grüßen")
        assert greeter.docstring == "Grüße, Welt"
        assert ("caller", "grüßen") in {
            (c.caller_symbol_id.rsplit("::", 1)[-1], c.target_name) for c in result.calls
        }
