"""god_class does not judge a Ruby ``module`` without ``initialize`` (mixin,
concern or ``module_function`` bag); a ``class`` is still judged."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.god_class import GodClassDetector
from repowise.core.analysis.health.complexity import walk_file


def _brain() -> str:
    # 9 branches over 80 lines: a brain method (nloc >= 70, ccn >= 9).
    filler = "\n".join(f"    v{i} = x + {i}" for i in range(70))
    arms = "\n".join(f"    return {i} if x == {i}" for i in range(9))
    return f"  def decide(x)\n{filler}\n{arms}\n    0\n  end\n"


def _small(i: int) -> str:
    body = "\n".join(f"    a{j} = @n + {j}" for j in range(8))
    return f"  def m{i}\n{body}\n    @n\n  end\n"


def _decl(head: str, extra: str = "") -> str:
    return f"{head}\n{extra}{_brain()}" + "".join(_small(i) for i in range(16)) + "end\n"


def _god_classes(src: str) -> list[str]:
    res = walk_file("lib/engine.rb", "ruby", src.encode())
    ctx = FileContext(
        file_path="lib/engine.rb",
        language="ruby",
        nloc=400,
        has_test_file=False,
        module=None,
        class_metrics=res.classes,
    )
    return [r.function_name for r in GodClassDetector().detect(ctx)]


@pytest.mark.parametrize(
    "extra",
    ["", "  extend ActiveSupport::Concern\n", "  module_function\n"],
    ids=["mixin", "concern", "module_function"],
)
def test_god_class_skips_ruby_module(extra: str):
    assert _god_classes(_decl("module Engine", extra)) == []


def test_god_class_still_flags_ruby_class():
    assert _god_classes(_decl("class Engine")) == ["Engine"]


def test_god_class_still_flags_ruby_module_with_initialize():
    init = "  def initialize(n)\n    @n = n\n  end\n"
    assert _god_classes(_decl("module Engine", init)) == ["Engine"]
