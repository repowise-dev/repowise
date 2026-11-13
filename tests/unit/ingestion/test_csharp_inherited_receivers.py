"""A typed C# receiver reaching a method its type inherits from a repository base."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

BASE = (
    "namespace Acme;\npublic abstract class BaseMessage {\n"
    "  public int CorrelationId() { return 1; }\n}\n"
)
REQUEST = "namespace Acme;\npublic abstract class BaseRequest : BaseMessage { }\n"
CONSUMER = (
    "namespace Acme;\npublic class Handler {\n"
    "  public void Run(GetRequest request) { var id = request.CorrelationId(); }\n}\n"
)
HERITAGE = {
    "src/GetRequest.cs::GetRequest": {"src/BaseRequest.cs::BaseRequest"},
    "src/BaseRequest.cs::BaseRequest": {"src/BaseMessage.cs::BaseMessage"},
}
INHERITED = ("src/BaseMessage.cs::BaseMessage::CorrelationId", "receiver_typed_global")


def _edges(tmp_path: Path, get_request: str, **extra: str) -> set[tuple[str, str]]:
    """``{(callee id, origin)}`` for the calls ``Handler.Run`` makes."""
    files = {
        "src/BaseMessage.cs": BASE,
        "src/BaseRequest.cs": REQUEST,
        "src/GetRequest.cs": get_request,
        "src/Handler.cs": CONSUMER,
        **extra,
    }
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
        parsed[rel] = parser.parse_file(info, text.encode())
    resolver = CallResolver(parsed, {}, repo_path=str(tmp_path), heritage_parents=HERITAGE)
    calls = parsed["src/Handler.cs"].calls
    return {(r.callee_id, r.origin) for r in resolver.resolve_file("src/Handler.cs", calls)}


def test_a_grandparent_method_answers(tmp_path: Path) -> None:
    edges = _edges(tmp_path, "namespace Acme;\npublic class GetRequest : BaseRequest { }\n")
    assert INHERITED in edges


def test_a_method_the_type_declares_hides_the_base(tmp_path: Path) -> None:
    get_request = (
        "namespace Acme;\npublic class GetRequest : BaseRequest {\n"
        "  public new int CorrelationId(int salt) { return salt; }\n}\n"
    )
    assert INHERITED not in _edges(tmp_path, get_request)


def test_two_types_of_the_name_refuse(tmp_path: Path) -> None:
    edges = _edges(
        tmp_path,
        "namespace Acme;\npublic class GetRequest : BaseRequest { }\n",
        **{"src/Other.cs": "namespace Other;\npublic class GetRequest { }\n"},
    )
    assert INHERITED not in edges


def test_an_override_between_refuses(tmp_path: Path) -> None:
    """Two ancestors declare the name; which one the compiler names is not settled here."""
    request = (
        "namespace Acme;\npublic abstract class BaseRequest : BaseMessage {\n"
        "  public int CorrelationId() { return 2; }\n}\n"
    )
    edges = _edges(
        tmp_path,
        "namespace Acme;\npublic class GetRequest : BaseRequest { }\n",
        **{"src/BaseRequest.cs": request},
    )
    assert not any(callee.endswith("::CorrelationId") for callee, _ in edges)


def test_a_base_method_that_cannot_take_the_arguments_is_not_the_callee(tmp_path: Path) -> None:
    consumer = (
        "namespace Acme;\npublic class Handler {\n"
        "  public void Run(GetRequest request) { var id = request.CorrelationId(1, 2); }\n}\n"
    )
    ext = (
        "namespace Acme;\npublic static class Ext {\n"
        "  public static int CorrelationId(this GetRequest r, int a, int b) { return a; }\n}\n"
    )
    edges = _edges(
        tmp_path,
        "namespace Acme;\npublic class GetRequest : BaseRequest { }\n",
        **{"src/Handler.cs": consumer, "src/Ext.cs": ext},
    )
    assert INHERITED not in edges
