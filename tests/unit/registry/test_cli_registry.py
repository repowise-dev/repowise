"""CLIRegistry behavior."""

from __future__ import annotations

import click
import pytest

from repowise.core.registry import CLIRegistry


def _make_command(name: str) -> click.Command:
    @click.command(name=name)
    def _cmd() -> None:  # pragma: no cover - never invoked
        pass

    return _cmd


@pytest.fixture
def registry() -> CLIRegistry:
    return CLIRegistry()


@pytest.fixture
def root() -> click.Group:
    @click.group()
    def cli() -> None:  # pragma: no cover
        pass

    return cli


def test_register_preserves_order(registry, root):
    a = _make_command("a")
    b = _make_command("b")
    c = _make_command("c")
    registry.register(a)
    registry.register(b)
    registry.register(c)
    registry.apply(root)
    assert list(root.commands.keys()) == ["a", "b", "c"]


def test_apply_is_idempotent_per_root(registry, root):
    registry.register(_make_command("once"))
    registry.apply(root)
    # Second call must not raise or duplicate.
    registry.apply(root)
    assert list(root.commands.keys()) == ["once"]


def test_apply_supports_multiple_roots(registry):
    registry.register(_make_command("shared"))

    @click.group()
    def root_a() -> None:  # pragma: no cover
        pass

    @click.group()
    def root_b() -> None:  # pragma: no cover
        pass

    registry.apply(root_a)
    registry.apply(root_b)
    assert "shared" in root_a.commands
    assert "shared" in root_b.commands


def test_register_with_parent_attaches_to_subgroup(registry, root):
    @click.group(name="grp")
    def subgroup() -> None:  # pragma: no cover
        pass

    registry.register(subgroup)
    leaf = _make_command("leaf")
    registry.register(leaf, parent=subgroup)
    registry.apply(root)
    assert "grp" in root.commands
    assert "leaf" in subgroup.commands


def test_reset_clears_entries(registry, root):
    registry.register(_make_command("x"))
    registry.reset()
    registry.apply(root)
    assert root.commands == {}
