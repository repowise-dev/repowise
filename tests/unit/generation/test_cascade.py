"""Unit tests for the cascade policy."""

from __future__ import annotations

from dataclasses import dataclass

from repowise.core.generation.cascade import (
    build_page_dependencies,
    expand_cascade,
)


@dataclass(frozen=True)
class _MG:
    """Duck-typed stand-in for selection.ModuleGroup."""

    key: str
    file_paths: tuple[str, ...]


def _deps():
    return build_page_dependencies(
        module_groups=[_MG("src", ("src/a.py", "src/b.py"))],
        scc_groups=[("scc-abc", ["src/a.py", "src/c.py"])],
        layer_page_of={"src/a.py": "layer_page:layer:core"},
        repo_wide_ids=(
            "repo_overview:demo",
            "architecture_diagram:demo",
            "onboarding:guided-tour",
        ),
    )


def test_containers_of_collects_module_scc_layer() -> None:
    deps = _deps()
    assert deps.containers_of("src/a.py") == {
        "module_page:src",
        "scc_page:scc-abc",
        "layer_page:layer:core",
    }
    # b.py is only in the module.
    assert deps.containers_of("src/b.py") == {"module_page:src"}


def test_cascade_none_marks_dependents_stale_generates_only_seed() -> None:
    deps = _deps()
    result = expand_cascade({"file_page:src/a.py"}, "none", deps)
    assert result.generate_ids == {"file_page:src/a.py"}
    assert result.stale_ids == {
        "module_page:src",
        "scc_page:scc-abc",
        "layer_page:layer:core",
        "repo_overview:demo",
        "architecture_diagram:demo",
        "onboarding:guided-tour",
    }


def test_cascade_dependents_regenerates_containers_marks_repo_wide_stale() -> None:
    deps = _deps()
    result = expand_cascade({"file_page:src/a.py"}, "dependents", deps)
    assert result.generate_ids == {
        "file_page:src/a.py",
        "module_page:src",
        "scc_page:scc-abc",
        "layer_page:layer:core",
    }
    assert result.stale_ids == {
        "repo_overview:demo",
        "architecture_diagram:demo",
        "onboarding:guided-tour",
    }


def test_cascade_full_regenerates_everything_marks_nothing() -> None:
    deps = _deps()
    result = expand_cascade({"file_page:src/a.py"}, "full", deps)
    assert result.generate_ids == {
        "file_page:src/a.py",
        "module_page:src",
        "scc_page:scc-abc",
        "layer_page:layer:core",
        "repo_overview:demo",
        "architecture_diagram:demo",
        "onboarding:guided-tour",
    }
    assert result.stale_ids == set()


def test_regenerated_page_never_also_marked_stale() -> None:
    # Seed the module page directly plus its file: the module must not be stale.
    deps = _deps()
    result = expand_cascade({"file_page:src/a.py", "module_page:src"}, "none", deps)
    assert "module_page:src" in result.generate_ids
    assert "module_page:src" not in result.stale_ids


def test_non_file_seed_has_no_file_cascade() -> None:
    deps = _deps()
    result = expand_cascade({"module_page:src"}, "dependents", deps)
    # A module-page seed drags no file containers in; repo-wide still decays.
    assert result.generate_ids == {"module_page:src"}
    assert result.stale_ids == {
        "repo_overview:demo",
        "architecture_diagram:demo",
        "onboarding:guided-tour",
    }
