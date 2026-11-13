"""Health metrics for Scala, pinned on code from upickle.

Each number below was counted by hand from the source. NLOC counts the body
from the line holding ``{``. The fixtures keep to ``if`` / ``while`` and
symbolic ``&&`` / ``||``: ``match`` arms and word operators are counted
differently by work still in flight.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

# upickle core RenderUtils.scala escapeChar0, implicits macros.scala handleType
# (its tail trimmed to one expression), ujson CharSequenceParser.scala.
_UPICKLE = r"""object RenderUtils {
  final def escapeChar0(i0: Int,
                        len: Int,
                        sb: upickle.core.CharBuilder,
                        s: CharSequence,
                        escapeUnicode: Boolean,
                        wrapQuotes: Boolean): upickle.core.CharBuilder = {
    var i = i0

    // Split the loop based on `escapeUnicode`,
    // to avoid repeating the check within the hot loop
    if (escapeUnicode){
      while (i < len) {
        val c = s.charAt(i)
        if (!CharUtils.appendEscapedChar(sb, c, i)) {
          if (c < ' ' || c > 127) { CharUtils.escapeSingleCharUnicodeEscape(sb, i, c) }
          else { i = CharUtils.appendSimpleStringSectionNoUnicode(sb, i, len, s) }
        }
        i += 1
      }
    }else{
      while (i < len) {
        val c = s.charAt(i)
        if (!CharUtils.appendEscapedChar(sb, c, i)) {
          if (c < ' ') { CharUtils.escapeSingleCharUnicodeEscape(sb, i, c) }
          else { i = CharUtils.appendSimpleStringSection(sb, i, len, s) }
        }
        i += 1
      }
    }

    if (wrapQuotes) sb.append('"')
    sb
  }

  def handleType(tpe: TypeRepr, name: String, skipTrait: Boolean): Option[Symbol] = {
    val AppliedType(typePrefix, List(arg)) = tpe: @unchecked

    if (skipTrait &&
        (arg.typeSymbol.flags.is(Flags.Trait) ||
          (arg.typeSymbol.flags.is(Flags.Abstract) && !arg.typeSymbol.flags.is(Flags.Enum)))){
      None
    } else {
      Some(Symbol.newVal(Symbol.spliceOwner, name, tpe, Flags.Lazy, Symbol.noSymbol))
    }
  }
}

final class CharSequenceParser[J](cs: CharSequence) extends CharParser[J]{
  override def growBuffer(until: Int): Unit = ()
  def readDataIntoBuffer(buffer: Array[Char], bufferOffset: Int) = {
    if(buffer == null) (cs.toString.toCharArray, cs.length == 0, cs.length)
    else (buffer, true, -1)
  }
  final def close() = ()
}
"""


def _walk(source: str):
    fcx = walk_file("src/Sample.scala", "scala", source.encode())
    if not fcx.functions:
        pytest.skip("tree-sitter grammar for Scala unavailable")
    return fcx


def test_upickle_functions() -> None:
    metrics = {
        f.name: (f.ccn, f.max_nesting, f.nloc, f.param_count)
        for f in _walk(_UPICKLE).functions
        if f.name in {"escapeChar0", "handleType"}
    }
    assert metrics == {
        # if, while, if, if + ``||``, while, if, if, if: 9 points. if > while >
        # if > if is the deepest path; 2 blank and 2 comment lines are not code.
        "escapeChar0": (10, 4, 24, 6),
        # One if with ``&& || &&``.
        "handleType": (5, 1, 10, 3),
    }


def test_compound_condition() -> None:
    conditions = [
        (f.name, c.line, c.operator_count, c.flat_chain)
        for f in _walk(_UPICKLE).functions
        for c in f.complex_conditions
        if c.operator_count >= 3
    ]
    assert conditions == [("handleType", 39, 3, False)]


def test_class_and_object_members() -> None:
    classes = {c.name: (c.method_count, c.total_nloc) for c in _walk(_UPICKLE).classes}
    assert classes == {
        # An ``object`` is measured like a class.
        "RenderUtils": (2, 41),
        "CharSequenceParser": (3, 8),
    }
