"""What a C++ pointer-like wrapper's ``operator->`` yields.

``shared_ptr<Foo> p`` denotes a ``Foo`` at every call the arrow reaches, so
taking the head answers ``shared_ptr`` and resolves nothing. Only the heads
that really do forward may be unwrapped: a container holds a ``T`` without
being one, and a repo's own handle does not say either way at the declaration.

``normalize_return_type`` deliberately does not use this, and the last test
holds that apart: there the head IS the receiver, so the same spelling wants
the opposite answer.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.return_types import normalize_return_type
from repowise.core.ingestion.type_names import POINTER_LIKE_MEMBERS, unwrap_pointer_like


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("std::shared_ptr<Foo>", "Foo"),
        ("shared_ptr<Foo>", "Foo"),
        ("std::unique_ptr<Foo>", "Foo"),
        ("unique_ptr<Foo, Deleter>", "Foo"),
        ("std::optional<Baz>", "Baz"),
        ("ns::unique_ptr<A::B>", "B"),
        ("std::shared_ptr<std::map<int, Bar>>", "map"),
    ],
)
def test_a_forwarding_head_yields_its_argument(written: str, expected: str) -> None:
    assert unwrap_pointer_like(written) == expected


@pytest.mark.parametrize(
    "written",
    [
        "std::vector<Foo>",
        "std::map<K, V>",
        "SharedHandle<Foo>",
        "std::weak_ptr<Foo>",
        "Foo",
        "shared_ptr",
        "shared_ptr<>",
        "",
    ],
)
def test_everything_else_forwards_nowhere(written: str) -> None:
    """The controls. ``weak_ptr`` declares no ``operator->`` at all."""
    assert unwrap_pointer_like(written) is None


def test_the_dot_reachable_members_are_named() -> None:
    """A caller blind to the operator refuses these, so the set is the contract."""
    assert {"get", "reset", "release", "use_count"} <= POINTER_LIKE_MEMBERS
    assert "sendMessage" not in POINTER_LIKE_MEMBERS


def test_a_return_type_keeps_its_wrapper() -> None:
    """``future<T>.get()`` is a method on ``future``, which is the other reading."""
    assert normalize_return_type("std::shared_ptr<Foo>", "cpp") == "shared_ptr"
