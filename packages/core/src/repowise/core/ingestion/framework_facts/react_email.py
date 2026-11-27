"""React Email: the directories its preview server renders."""

from __future__ import annotations

import posixpath
import shlex
from typing import TYPE_CHECKING

from .base import FrameworkFacts, _js

if TYPE_CHECKING:
    from .npm import NpmManifest

#: The React Email preview server (``email dev``) renders the default export of
#: every file in its emails directory; nothing imports a preview component.
REACT_EMAIL = FrameworkFacts(
    name="React Email",
    anchor="framework:react-email",
    npm_packages=("react-email", "@react-email/preview-server"),
    entry_globs=_js("emails/*"),  # re-rooted at a ``--dir`` (:func:`js_frameworks`)
)

_EMAIL_COMMANDS = frozenset({"dev", "build", "export"})


#: Tokens that end one shell command, so a later command's flags are not read.
_SHELL_SEPARATORS = frozenset({"&&", "||", ";", "|", "&"})


def _email_script_dir(args: list[str]) -> str:
    """The ``--dir`` among one ``email`` command's *args*, else the default ``emails``."""
    for j, arg in enumerate(args):
        if arg in _SHELL_SEPARATORS:
            break
        if arg in ("--dir", "-d") and j + 1 < len(args):
            return posixpath.normpath(args[j + 1])
        if arg.startswith("--dir="):
            return posixpath.normpath(arg.removeprefix("--dir="))
    return "emails"


def _react_email_dirs(manifest: NpmManifest) -> list[str]:
    """Every directory an ``email dev|build|export`` script reads, sorted.

    A command without ``--dir`` reads the default ``emails``, as does a
    package with no such script. ``.`` is the package itself; a directory
    outside the package is dropped, since nothing in it can be anchored.
    """
    dirs: set[str] = set()
    for script in manifest.scripts.values():
        try:
            words = shlex.split(script)
        except ValueError:
            continue
        for i in range(len(words) - 1):
            if words[i] == "email" and words[i + 1] in _EMAIL_COMMANDS:
                dirs.add(_email_script_dir(words[i + 2 :]))
    return sorted(d for d in (dirs or {"emails"}) if not d.startswith(("..", "/")))
