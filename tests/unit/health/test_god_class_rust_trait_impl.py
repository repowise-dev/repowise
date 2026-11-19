"""god_class does not judge a Rust ``impl Trait for T``: the trait fixes its method set."""

from __future__ import annotations

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.god_class import GodClassDetector
from repowise.core.analysis.health.complexity import walk_file


def _brain() -> str:
    # 9 branches over 80 lines: a brain method (nloc >= 70, ccn >= 9).
    arms = "\n".join(f"            if x == {i} {{ return {i}; }}" for i in range(9))
    filler = "\n".join(f"        let _v{i} = x + {i};" for i in range(70))
    return f"    fn decide(&self, x: i32) -> i32 {{\n{filler}\n        {{\n{arms}\n        }}\n        0\n    }}\n"


def _small(i: int) -> str:
    body = "\n".join(f"        let a{j} = self.n + {j};" for j in range(8))
    return f"    fn m{i}(&self) -> i32 {{\n{body}\n        self.n\n    }}\n"


def _impl(head: str) -> str:
    return f"{head} {{\n{_brain()}" + "".join(_small(i) for i in range(16)) + "}\n"


def _god_classes(src: str) -> list[str]:
    res = walk_file("src/engine.rs", "rust", src.encode())
    ctx = FileContext(
        file_path="src/engine.rs",
        language="rust",
        nloc=400,
        has_test_file=False,
        module=None,
        class_metrics=res.classes,
    )
    return [r.function_name for r in GodClassDetector().detect(ctx)]


def test_god_class_skips_rust_trait_impl():
    src = "struct Engine { n: i32 }\ntrait Visitor { fn decide(&self, x: i32) -> i32; }\n"
    src += _impl("impl Visitor for Engine")
    assert _god_classes(src) == []


def test_god_class_still_flags_rust_inherent_impl():
    src = "struct Engine { n: i32 }\n" + _impl("impl Engine")
    assert _god_classes(src) == ["Engine"]
