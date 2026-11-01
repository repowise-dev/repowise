"""Where a repository's repowise index lives.

One resolution point for the directory every layer reads and writes: the
repo-local ``.repowise/`` by default, or a per-checkout entry under
``~/.repowise/repos/`` when the global store mode is on.

Global store mode is for repositories where the person indexing them is not in
a position to make repository-wide changes (issue #1551). It indexes a checkout
without creating ``.repowise/`` inside it, so there is nothing new to gitignore
and nothing new in ``git status``. The entry is keyed on the checkout's
absolute path, so two checkouts of one repository are two entries and the same
checkout always resolves to the same entry whichever directory the command runs
from.

The switch is per run: ``repowise init --global-store``, or
``REPOWISE_GLOBAL_STORE=1`` for the CI and multi-repo case, with
``--local-store`` / ``REPOWISE_GLOBAL_STORE=0`` for an explicit off. It is
deliberately not a ``config.yaml`` key: that file lives in the store this
module is choosing, so it cannot also choose it. What carries the decision
past the run that made it is the store itself. With the switch unset, a
checkout whose global entry already exists keeps resolving to that entry, which
is what lets a plain ``repowise update`` after a global ``init`` find the index
instead of creating a ``.repowise/`` the user asked not to have.

``REPOWISE_GLOBAL_STORE_ROOT`` relocates the store root (default
``~/.repowise/repos``). Tests use it so they never write into a real home
directory, and it is there for a home partition too small for every index.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
from collections.abc import Iterator
from pathlib import Path

__all__ = [
    "GLOBAL_STORE_ROOT_ENV",
    "GLOBAL_STORE_SWITCH_ENV",
    "HOME_DIRNAME",
    "LOCAL_STORE_DIRNAME",
    "global_store_dir",
    "global_store_mode",
    "global_store_root",
    "repo_store_key",
    "resolve_store_dir",
    "store_is_repo_local",
    "use_global_store",
]

#: Name of a repository's own index directory. Unchanged by this module.
LOCAL_STORE_DIRNAME = ".repowise"

#: Directory name of the user-global area, which already holds cross-repo
#: state (the update-check cache, ``platform.json``, the telemetry spool).
#: The global store lives *under* it, so ``rm -rf ~/.repowise`` stays one
#: honest reset of everything repowise keeps per machine.
HOME_DIRNAME = ".repowise"

#: Subdirectory of the above, so the store never has to share a namespace with
#: those machine-wide files.
GLOBAL_STORE_SUBDIR = "repos"

#: The switch and its root override. The switch takes "1"/"true"/"yes"/"on"
#: for on and "0"/"false"/"no" for off, the same vocabulary
#: ``REPOWISE_SKIP_EDITOR_SETUP`` uses; anything else is unset.
GLOBAL_STORE_SWITCH_ENV = "REPOWISE_GLOBAL_STORE"
GLOBAL_STORE_ROOT_ENV = "REPOWISE_GLOBAL_STORE_ROOT"

_ON = ("1", "true", "yes", "on")
_OFF = ("0", "false", "no")

#: Longest basename slug kept in an entry name. The digest beside it is what
#: makes the name unique; the slug is only there to make
#: ``ls ~/.repowise/repos`` readable.
_SLUG_MAX = 40

#: Hex characters of the path digest. 64 bits of a SHA-256, which no realistic
#: number of checkouts on one machine will collide on, and short enough to keep
#: even the deepest artifact path under any OS limit.
_DIGEST_CHARS = 16


def global_store_mode() -> str:
    """The switch as one of ``"on"``, ``"off"`` or ``"auto"``.

    ``"auto"`` (nothing set) keeps the pre-existing behaviour for every
    repository unless the checkout already has an entry, which
    :func:`resolve_store_dir` looks for. Nothing here reads a config file: the
    switch cannot live in the store it selects.
    """
    raw = os.environ.get(GLOBAL_STORE_SWITCH_ENV, "").strip().lower()
    if raw in _ON:
        return "on"
    if raw in _OFF:
        return "off"
    return "auto"


def global_store_root() -> Path:
    """Root of the global store: ``REPOWISE_GLOBAL_STORE_ROOT`` or ``~/.repowise/repos``."""
    override = os.environ.get(GLOBAL_STORE_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / HOME_DIRNAME / GLOBAL_STORE_SUBDIR


def repo_store_key(repo_path: str | Path) -> str:
    """Stable directory name for the checkout at *repo_path*.

    The digest is what makes it correct: the same checkout from any working
    directory hashes to the same key, and two checkouts of one repository (two
    absolute paths) hash to two. The basename slug is only for legibility.
    """
    absolute = Path(repo_path).expanduser().resolve()
    digest = hashlib.sha256(str(absolute).encode("utf-8")).hexdigest()[:_DIGEST_CHARS]
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", absolute.name).strip("-._") or "repo"
    return f"{slug[:_SLUG_MAX]}-{digest}"


def global_store_dir(repo_path: str | Path) -> Path:
    """The global-mode entry for *repo_path*. Not created, not checked."""
    return global_store_root() / repo_store_key(repo_path)


def resolve_store_dir(repo_path: str | Path) -> Path:
    """The directory *repo_path*'s index lives in.

    This is the one function that answers the question, so a mode that moves
    the index cannot be honoured by half the callers. Order:

    1. Switch on: the global entry, whether or not anything is there yet.
    2. Switch explicitly off: the repo-local path, with no auto-detection.
    3. Otherwise a repo-local ``.repowise/``: unchanged behaviour for every
       repository that already has one.
    4. Otherwise an existing global entry: the checkout was indexed in global
       mode, so ``update``, ``status``, ``doctor`` and ``mcp`` follow it
       without the switch, and the repository still never gains a
       ``.repowise/``.
    5. Otherwise the repo-local path, as before.
    """
    local = Path(repo_path) / LOCAL_STORE_DIRNAME
    mode = global_store_mode()
    if mode == "on":
        return global_store_dir(repo_path)
    if mode == "off":
        return local
    if local.is_dir():
        return local
    global_dir = global_store_dir(repo_path)
    if global_dir.is_dir():
        return global_dir
    return local


def store_is_repo_local(repo_path: str | Path) -> bool:
    """Whether the resolved store sits inside *repo_path*'s working tree.

    Callers that would otherwise add a ``.gitignore`` rule for a store file ask
    this first. Those rules exist to keep index artifacts out of ``git status``,
    and a store under ``$HOME`` cannot appear there, so writing one would be a
    working-tree edit the mode promises not to make.
    """
    return resolve_store_dir(repo_path) == Path(repo_path) / LOCAL_STORE_DIRNAME


@contextlib.contextmanager
def use_global_store(enabled: bool = True, root: str | Path | None = None) -> Iterator[None]:
    """Set the switch for the duration of the block, then restore it.

    Everything that resolves a store takes only a repo path, so the run's
    decision is carried in the environment rather than threaded as an argument
    through several hundred call sites. Restoration is in a ``finally``: the
    CLI's own test suite runs many commands per interpreter, and a mode left
    set would move every later command's store too.
    """
    saved = {name: os.environ.get(name) for name in (GLOBAL_STORE_SWITCH_ENV, GLOBAL_STORE_ROOT_ENV)}
    os.environ[GLOBAL_STORE_SWITCH_ENV] = "1" if enabled else "0"
    if root is not None:
        os.environ[GLOBAL_STORE_ROOT_ENV] = str(root)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
