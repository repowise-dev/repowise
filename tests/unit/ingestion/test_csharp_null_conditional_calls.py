"""A C# null-conditional call (``a?.Foo()``) must produce a call site.

``csharp.scm``'s four invocation patterns all anchor on
``member_access_expression``. tree-sitter-c-sharp spells ``a?.Foo()``
differently: an ``invocation_expression`` whose ``function`` is a
``conditional_access_expression`` wrapping a ``member_binding_expression``.
No pattern matched either node type, so the call was not resolved-and-wrong
and not unresolved either. It was never captured, which means nothing
downstream knew it existed -- it does not appear in the unresolved bucket
that would otherwise flag it. ``a?.Foo()`` is ordinary modern C#, sized
previously at roughly 7% of Ocelot's invocation nodes.

The new pattern mirrors the member-call pattern's receiver and target
capture names, so the existing receiver-typing tiers take it with no
resolver change. It is deliberately scoped to the plain identifier
receiver: a chained or constructed receiver inside the conditional access
raises its own receiver-typing question, which belongs with the patterns
that already answer it.

One limitation is pre-existing, orthogonal, and NOT addressed here. The
receiver-type scan does not read a C# nullable annotation, so
``Logger? logger`` types nothing -- and that is equally true of a plain
``logger.LogInformation(...)`` on unmodified main. It is a typing gap, not
a capture gap, and closing it would be a resolution change rather than the
additive capture fix this issue asks for.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

LOGGER = """\
namespace Acme;
public class Logger { public void LogInformation(string m) { } }
"""


def _build(tmp_path: Path, files: dict[str, str]):
    """Parse *files* into a resolver, returning ``(parsed, resolver)``."""
    parser = ASTParser()
    parsed = {}
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        info = FileInfo(
            path=rel,
            abs_path=str(path),
            language="csharp",
            size_bytes=len(text),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        parsed[rel] = parser.parse_file(info, text.encode("utf-8"))
    resolver = CallResolver(parsed, {}, repo_path=str(tmp_path))
    return parsed, resolver


def _sites(parsed, caller: str) -> list[tuple[str, str | None, int]]:
    return [(c.target_name, c.receiver_name, c.line) for c in parsed[caller].calls]


class TestTheCallSiteIsCaptured:
    """The issue's repro: the call must be captured at all."""

    def test_the_issue_repro_produces_a_call_site(self, tmp_path: Path) -> None:
        """``logger?.LogInformation("done")`` reaches the resolver as a call.

        Before this pattern the shape matched nothing, so the call site was
        never produced. Asserting on the parser's output rather than on an
        edge is the point: an absent site is invisible downstream, where a
        low-confidence one would at least be countable.
        """
        src = (
            "namespace Acme;\n"
            "public class Consumer\n"
            "{\n"
            "    public void Notify(ILogger? logger)\n"
            "    {\n"
            '        logger?.LogInformation("done");\n'
            "    }\n"
            "}\n"
        )
        parsed, _ = _build(tmp_path, {"src/Consumer.cs": src})
        assert _sites(parsed, "src/Consumer.cs") == [("LogInformation", "logger", 6)]

    def test_a_generic_target_keeps_the_bare_name(self, tmp_path: Path) -> None:
        """A type argument list wraps the name in a ``generic_name``.

        The capture sits inside each branch for the reason the simple-call
        pattern's does: capturing the alternation itself would name the
        target ``Log<T>``, which no index holds.
        """
        src = (
            "namespace Acme;\n"
            "public class Consumer { public void N(Logger logger) { logger?.Log<Logger>(); } }\n"
        )
        parsed, _ = _build(tmp_path, {"src/Consumer.cs": src})
        assert _sites(parsed, "src/Consumer.cs") == [("Log", "logger", 2)]

    def test_the_conditional_and_plain_forms_do_not_duplicate(self, tmp_path: Path) -> None:
        """The new pattern must not double up with the member-call pattern.

        A call matched twice -- once with a receiver, once without -- gets
        resolved twice, and the receiver-less copy can mint a wrong edge
        beside the right one.
        """
        src = (
            "namespace Acme;\n"
            "public class Consumer\n"
            "{\n"
            '    public void N(Logger logger) { logger?.LogInformation("a"); logger.LogInformation("b"); }\n'
            "}\n"
        )
        parsed, _ = _build(tmp_path, {"src/Consumer.cs": src})
        sites = [(c.line, c.target_name) for c in parsed["src/Consumer.cs"].calls]
        # Same line, same target: the two spellings dedupe to one record.
        assert sites == [(4, "LogInformation")]


class TestTheExistingTiersResolveIt:
    """No resolver change: the capture names are the member-call pattern's."""

    def _logger_repo(self, tmp_path: Path, body: str) -> dict[str, str]:
        return {
            "src/Logger.cs": LOGGER,
            "src/Consumer.cs": f"namespace Acme;\npublic class Consumer {{\n    {body}\n}}\n",
        }

    def test_it_resolves_on_the_typed_receiver_tier(self, tmp_path: Path) -> None:
        files = self._logger_repo(
            tmp_path,
            'public void N(Logger logger) { logger?.LogInformation("done"); }',
        )
        parsed, resolver = _build(tmp_path, files)
        edges = [
            (rc.callee_id, rc.origin, rc.confidence)
            for rc in resolver.resolve_file("src/Consumer.cs", parsed["src/Consumer.cs"].calls)
        ]
        assert ("src/Logger.cs::Logger::LogInformation", "receiver_typed_global", 0.75) in edges

    def test_a_var_typed_receiver_resolves_too(self, tmp_path: Path) -> None:
        """A receiver typed from ``new`` is a different shape of the same tier."""
        files = self._logger_repo(
            tmp_path,
            'public void N() { var logger = new Logger(); logger?.LogInformation("done"); }',
        )
        parsed, resolver = _build(tmp_path, files)
        edges = [
            (rc.callee_id, rc.origin, rc.confidence)
            for rc in resolver.resolve_file("src/Consumer.cs", parsed["src/Consumer.cs"].calls)
        ]
        assert ("src/Logger.cs::Logger::LogInformation", "receiver_typed_global", 0.75) in edges


class TestTheScopeIsHeld:
    """Only the plain identifier receiver. The rest raise typing questions."""

    def test_a_member_access_receiver_produces_no_site(self, tmp_path: Path) -> None:
        """``a.b?.M()`` needs the type of ``a.b``, which this pattern cannot ask."""
        src = "namespace Acme;\npublic class C { public void N() { holder.logger?.Log(\"x\"); } }\n"
        parsed, _ = _build(tmp_path, {"src/Consumer.cs": src})
        assert _sites(parsed, "src/Consumer.cs") == []

    def test_an_index_receiver_produces_no_site(self, tmp_path: Path) -> None:
        src = "namespace Acme;\npublic class C { public void N() { loggers?[0].Log(\"x\"); } }\n"
        parsed, _ = _build(tmp_path, {"src/Consumer.cs": src})
        assert _sites(parsed, "src/Consumer.cs") == []

    def test_a_chained_conditional_produces_only_its_inner_call(self, tmp_path: Path) -> None:
        """``x?.M()?.N()`` is two questions; this fix answers the first only."""
        src = "namespace Acme;\npublic class C { public void N() { x?.Log(\"a\")?.More(); } }\n"
        parsed, _ = _build(tmp_path, {"src/Consumer.cs": src})
        assert _sites(parsed, "src/Consumer.cs") == [("Log", "x", 2)]

    def test_a_plain_member_call_is_unaffected(self, tmp_path: Path) -> None:
        """The control: the shape that already worked must still work."""
        src = "namespace Acme;\npublic class C { public void N(Logger l) { l.LogInformation(\"x\"); } }\n"
        parsed, _ = _build(tmp_path, {"src/Consumer.cs": src})
        assert _sites(parsed, "src/Consumer.cs") == [("LogInformation", "l", 2)]
