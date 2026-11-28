"""primitive_obsession on methods a same-file supertype declares.

PHP, Scala and Swift implement an interface method, an abstract ``def`` or a
protocol requirement with no ``override`` keyword. When the supertype is
declared in the same file, its declaration owns the parameter list.
"""

from __future__ import annotations

from repowise.core.analysis.health.complexity import FunctionComplexity, walk_file_complexity


def _fixed(path: str, language: str, source: str) -> dict[tuple[str, int], bool]:
    fns: list[FunctionComplexity] = walk_file_complexity(path, language, source.encode())
    return {(f.name, f.start_line): f.signature_fixed for f in fns}


_PHP = """<?php
namespace App;
interface Prompt { public function select(string $q, array $c, bool $d, int $a, bool $m, string $e); }
interface Io extends Prompt {}
abstract class Base implements Io { abstract protected function hook(int $a, int $b, int $c, int $d, int $e); }
class Console extends Base implements \\Countable {
    public function select(string $q, array $c, bool $d, int $a, bool $m, string $e) {}
    protected function hook(int $a, int $b, int $c, int $d, int $e) {}
    public function render(int $a, int $b, int $c, int $d, int $e) {}
    public function count(): int { return 0; }
}
"""


def test_php_interface_and_abstract_methods_are_fixed() -> None:
    assert _fixed("src/Console.php", "php", _PHP) == {
        ("select", 7): True,
        ("hook", 8): True,
        ("render", 9): False,
        # ``Countable`` is declared elsewhere.
        ("count", 10): False,
    }


_SCALA = """
trait Visitor[T] { def visitFloat(d: Double, decIndex: Int, expIndex: Int, index: Int, raw: Boolean): T }
abstract class Base[T] extends Visitor[T]
object JsVisitor extends Base[Unit] {
  def visitFloat(d: Double, decIndex: Int, expIndex: Int, index: Int, raw: Boolean): Unit = ()
  def visitInt(a: Int, b: Int, c: Int, d: Int, e: Int): Unit = ()
}
class JsVisitor(x: Int) extends Visitor[Unit] {
  def visitInt(a: Int, b: Int, c: Int, d: Int, e: Int): Unit = ()
}
"""


def test_scala_trait_member_is_fixed_and_the_companion_is_not_a_supertype() -> None:
    assert _fixed("src/JsVisitor.scala", "scala", _SCALA) == {
        ("visitFloat", 5): True,
        ("visitInt", 6): False,
        # The class shares the object's name, not its members.
        ("visitInt", 9): False,
    }


_SWIFT = """
public protocol Monitor: Sendable {
    func sent(_ task: Task, bytes: Int64, total: Int64, expected: Int64, index: Int)
    func received(_ task: Task, bytes: Int64, total: Int64, expected: Int64, index: Int)
}
extension Monitor {
    func sent(_ task: Task, bytes: Int64, total: Int64, expected: Int64, index: Int) {}
}
final class Composite {
    func received(_ task: Task, bytes: Int64, total: Int64, expected: Int64, index: Int) {}
    func other(a: Int, b: Int, c: Int, d: Int, e: Int) {}
    private func received(_ task: Task, bytes: Int64, total: Int64, expected: Int64, index: Int, n: Int) {}
}
extension Composite: Monitor {}
"""


def test_swift_protocol_requirements_are_fixed() -> None:
    assert _fixed("Sources/Monitor.swift", "swift", _SWIFT) == {
        ("sent", 7): True,
        ("received", 10): True,
        ("other", 11): False,
        # An overload with another parameter count is no requirement.
        ("received", 12): False,
    }
