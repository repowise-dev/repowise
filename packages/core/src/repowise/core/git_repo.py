"""GitPython's ``Repo`` without the Windows ``gc.collect()`` on close."""

from __future__ import annotations

import contextlib

import git
import gitdb.util


class Repo(git.Repo):
    """A ``git.Repo`` whose ``close()`` (and so ``__del__``) skips garbage collection.

    GitPython's close runs two full ``gc.collect()`` passes on Windows so a
    caller can delete the checkout. Nothing here deletes one, and with a parsed
    graph on the heap each pass costs about a second.
    """

    def close(self) -> None:
        if self.git:
            with contextlib.suppress(Exception):
                self.git.clear_cache()
            with contextlib.suppress(Exception):
                gitdb.util.mman.collect()
