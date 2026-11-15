"""The directory trie the zoom tree's folder tier is built from.

Mutable during construction: :func:`insert` files by path, then
:func:`compress` collapses single-child folder chains as the tree is emitted.
"""

from __future__ import annotations


class Dir:
    __slots__ = ("files", "name", "path", "subdirs")

    def __init__(self, name: str, path: str) -> None:
        self.name = name
        self.path = path
        self.subdirs: dict[str, Dir] = {}
        self.files: list[str] = []


def insert(root: Dir, path: str) -> None:
    parts = path.split("/")
    *dirs, _fname = parts
    if not _fname:
        # Malformed (empty or trailing-slash) path: skip rather than mint a
        # blank-named leaf. The architecture view never emits these.
        return
    cur = root
    acc: list[str] = []
    for d in dirs:
        acc.append(d)
        key = "/".join(acc)
        nxt = cur.subdirs.get(d)
        if nxt is None:
            nxt = Dir(name=d, path=key)
            cur.subdirs[d] = nxt
        cur = nxt
    cur.files.append(path)


def compress(node: Dir) -> tuple[str, Dir, list[str]]:
    """Collapse single-child folder chains.

    Returns ``(joined-name, deepest folder, every path the chain swallowed)``
    so ``a/(only)b/(only)c`` becomes one node named ``a/b/c``. The path list is
    ordered shallowest-first and is what lets a caller ask whether *any* rung of
    the chain is a directory something knows about: the collapsed rungs are real
    directories that simply have no branch of their own, and a repository whose
    source sits under `packages/x/src/pkg/name` collapses most of its documented
    directories into exactly such a chain.
    """
    name = node.name
    covered = [node.path]
    while len(node.subdirs) == 1 and not node.files:
        (only,) = node.subdirs.values()
        name = f"{name}/{only.name}"
        node = only
        covered.append(node.path)
    return name, node, covered
