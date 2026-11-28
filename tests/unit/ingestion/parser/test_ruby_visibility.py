"""Ruby method visibility: ``private`` and ``protected`` are calls in the class body."""

from __future__ import annotations

import pytest

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info


def _visibility(parser: ASTParser, body: str) -> dict[str, str]:
    result = parser.parse_file(_make_file_info("lib/box.rb", "ruby"), body.encode())
    return {s.name: s.visibility for s in result.symbols if s.kind in ("method", "function")}


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # A bare keyword sets the access of every def after it.
        (
            "class Box\n  def a; end\n  private\n  def b; end\nend\n",
            {"a": "public", "b": "private"},
        ),
        (
            "class Box\n  protected\n  def a; end\n  public\n  def b; end\nend\n",
            {"a": "protected", "b": "public"},
        ),
        # A call naming the method wins, whichever side of the def it sits on.
        (
            "class Box\n  def a; end\n  def b; end\n  private :a\nend\n",
            {"a": "private", "b": "public"},
        ),
        (
            "class Box\n  private\n  def a; end\n  public :a\nend\n",
            {"a": "public"},
        ),
        ("class Box\n  def a; end\n  private 'a'\nend\n", {"a": "private"}),
        # ``private def x`` covers that def only.
        (
            "class Box\n  private def a; end\n  def b; end\nend\n",
            {"a": "private", "b": "public"},
        ),
        # A nested class starts public again.
        (
            "class Box\n  private\n  class Inner\n    def a; end\n  end\nend\n",
            {"a": "public"},
        ),
        # ``def self.x`` ignores a bare ``private``; ``private_class_method`` is its own.
        (
            "class Box\n  private\n  def self.a; end\n  def self.b; end\n"
            "  private_class_method :b\nend\n",
            {"a": "public", "b": "private"},
        ),
        # ``class << self`` has a scope of its own.
        (
            "class Box\n  class << self\n    private\n    def a; end\n  end\n  def b; end\nend\n",
            {"a": "private", "b": "public"},
        ),
        # A def outside a class or module body is not touched.
        ("def a; end\n", {"a": "public"}),
    ],
)
def test_ruby_access_comes_from_the_class_body(
    parser: ASTParser, body: str, expected: dict[str, str]
) -> None:
    assert _visibility(parser, body) == expected
