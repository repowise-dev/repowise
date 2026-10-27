"""Lexically scoped bare-name call resolution.

A bare, receiver-less name in Elixir can only mean the caller's own module, a
module an explicit ``import`` brought in, or Kernel. ``alias`` / ``require`` /
``use`` bind a module name and never a function name, so the resolver must not
answer such a call from repo-wide uniqueness or from a non-import edge.

F# is the same rule with different spelling: a bare name means the enclosing
scope, a module the file has ``open``ed, or FSharp.Core. Both languages are in
``_LEXICAL_BARE_NAME_LANGUAGES``, so both are exercised here.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import parse_file


def _file_info(rel: str, abs_: Path, language: str = "elixir") -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=str(abs_),
        language=language,
        size_bytes=abs_.stat().st_size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _parse_all(
    tmp_path: Path, files: dict[str, str], language: str = "elixir"
) -> dict[str, ParsedFile]:
    out: dict[str, ParsedFile] = {}
    for rel, content in files.items():
        abs_ = tmp_path / rel
        abs_.parent.mkdir(parents=True, exist_ok=True)
        abs_.write_text(content, encoding="utf-8")
        out[rel] = parse_file(_file_info(rel, abs_, language), content.encode("utf-8"))
    return out


def _link(parsed: dict[str, ParsedFile], importer: str, module_path: str, target: str) -> None:
    """Stamp the resolved file on one import, as GraphBuilder does before resolution."""
    for imp in parsed[importer].imports:
        if imp.module_path == module_path:
            imp.resolved_file = target
            return
    raise AssertionError(f"no import of {module_path} in {importer}")


def _edges(parsed, tmp_path, import_targets):
    resolver = CallResolver(parsed, import_targets, repo_path=str(tmp_path))
    return [
        (rc.caller_id, rc.callee_id, rc.origin)
        for path, pf in parsed.items()
        for rc in resolver.resolve_file(path, pf.calls)
    ]


CONNECT = """\
defmodule Shop.Tenants.Connect do
  def connect(socket, params, opts), do: {socket, params, opts}
end
"""


class TestAliasDoesNotImportFunctionNames:
    def test_alias_never_answers_a_bare_call(self, tmp_path: Path) -> None:
        # `alias Shop.Tenants.Connect` makes the capitalised module
        # reachable as `Connect.foo`. It says nothing about a bare, lowercase
        # `connect/3`, which here is Phoenix.ChannelTest's.
        files = {
            "lib/connect.ex": CONNECT,
            "test/channel_test.exs": """\
defmodule ChannelTest do
  alias Shop.Tenants.Connect

  def run do
    connect(UserSocket, %{}, [])
  end
end
""",
        }
        parsed = _parse_all(tmp_path, files)
        _link(parsed, "test/channel_test.exs", "Shop.Tenants.Connect", "lib/connect.ex")
        edges = _edges(
            parsed, tmp_path, {"test/channel_test.exs": {"lib/connect.ex"}, "lib/connect.ex": set()}
        )
        assert not [e for e in edges if e[1].endswith("::connect")], edges

    def test_an_explicit_import_does_answer_it(self, tmp_path: Path) -> None:
        # The control: `import` is the one directive that brings bare function
        # names into scope, and it must keep working.
        files = {
            "lib/connect.ex": CONNECT,
            "test/channel_test.exs": """\
defmodule ChannelTest do
  import Shop.Tenants.Connect

  def run do
    connect(UserSocket, %{}, [])
  end
end
""",
        }
        parsed = _parse_all(tmp_path, files)
        _link(parsed, "test/channel_test.exs", "Shop.Tenants.Connect", "lib/connect.ex")
        edges = _edges(
            parsed, tmp_path, {"test/channel_test.exs": {"lib/connect.ex"}, "lib/connect.ex": set()}
        )
        assert (
            "test/channel_test.exs::ChannelTest::run",
            "lib/connect.ex::Shop.Tenants.Connect::connect",
            "import_merged",
        ) in edges, edges

    def test_use_does_not_answer_it_either(self, tmp_path: Path) -> None:
        # `use` runs a macro that may itself inject an `import`, which no
        # parser can see. The honest answer is no edge, not a guessed one.
        files = {
            "lib/connect.ex": CONNECT,
            "test/channel_test.exs": """\
defmodule ChannelTest do
  use Shop.Tenants.Connect

  def run do
    connect(UserSocket, %{}, [])
  end
end
""",
        }
        parsed = _parse_all(tmp_path, files)
        _link(parsed, "test/channel_test.exs", "Shop.Tenants.Connect", "lib/connect.ex")
        edges = _edges(
            parsed, tmp_path, {"test/channel_test.exs": {"lib/connect.ex"}, "lib/connect.ex": set()}
        )
        assert not [e for e in edges if e[1].endswith("::connect")], edges


class TestGlobalUniqueRefusal:
    def test_a_uniquely_named_function_elsewhere_is_not_the_answer(
        self, tmp_path: Path
    ) -> None:
        # `execute` here is Ecto.Migration's macro, in scope through
        # `use Ecto.Migration`. That exactly one app module also defines
        # `execute` is a coincidence, not evidence.
        files = {
            "lib/telemetry.ex": """\
defmodule Shop.Telemetry do
  def execute(event), do: event
end
""",
            "priv/migrations/20240919163303_add_payload.exs": """\
defmodule Shop.Repo.Migrations.AddPayload do
  use Ecto.Migration

  def change do
    execute("alter table messages add column payload jsonb")
  end
end
""",
        }
        parsed = _parse_all(tmp_path, files)
        edges = _edges(parsed, tmp_path, {})
        assert not [e for e in edges if e[1].endswith("::execute")], edges

    def test_a_same_module_bare_call_still_resolves(self, tmp_path: Path) -> None:
        # The refusals above must not reach the one tier Elixir's own scoping
        # rules do license: a bare name defined in the caller's own module.
        files = {
            "lib/app.ex": """\
defmodule Shop.Application do
  def start(_type, _args) do
    setup_region_mapping()
  end

  defp setup_region_mapping, do: :ok
end
""",
        }
        parsed = _parse_all(tmp_path, files)
        edges = _edges(parsed, tmp_path, {})
        assert (
            "lib/app.ex::Shop.Application::start",
            "lib/app.ex::Shop.Application::setup_region_mapping",
            "same_file",
        ) in edges, edges


TOKENS = """module Acme.Tokens

let parseToken (raw: string) = raw
"""


class TestFsharpBareNameScope:
    def test_a_uniquely_named_function_elsewhere_is_not_the_answer(
        self, tmp_path: Path
    ) -> None:
        # Nothing in Reader.fs brings `parseToken` into scope. That exactly
        # one other file defines the name is a coincidence, not evidence.
        files = {
            "src/Tokens.fs": TOKENS,
            "src/Reader.fs": """module Acme.Reader

let read raw = parseToken raw
""",
        }
        parsed = _parse_all(tmp_path, files, "fsharp")
        edges = _edges(parsed, tmp_path, {})
        assert not [e for e in edges if e[1].endswith("::parseToken")], edges

    def test_an_open_module_does_answer_it(self, tmp_path: Path) -> None:
        # The control: `open` binds every public name in the module, which is
        # the wildcard sentinel the merged-import tier reads.
        files = {
            "src/Tokens.fs": TOKENS,
            "src/Reader.fs": """module Acme.Reader

open Acme.Tokens

let read raw = parseToken raw
""",
        }
        parsed = _parse_all(tmp_path, files, "fsharp")
        _link(parsed, "src/Reader.fs", "Acme.Tokens", "src/Tokens.fs")
        edges = _edges(
            parsed, tmp_path, {"src/Reader.fs": {"src/Tokens.fs"}, "src/Tokens.fs": set()}
        )
        assert (
            "src/Reader.fs::read",
            "src/Tokens.fs::parseToken",
            "import_merged",
        ) in edges, edges

    def test_a_same_file_bare_call_still_resolves(self, tmp_path: Path) -> None:
        files = {
            "src/Tokens.fs": """module Acme.Tokens

let private normalise raw = raw

let parseToken raw = normalise raw
""",
        }
        parsed = _parse_all(tmp_path, files, "fsharp")
        edges = _edges(parsed, tmp_path, {})
        assert (
            "src/Tokens.fs::parseToken",
            "src/Tokens.fs::normalise",
            "same_file",
        ) in edges, edges
