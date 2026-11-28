"""Include directories from one JSON compilation database entry.

Both compiler families that write these databases are read:

- GCC and Clang: ``-I``, ``-isystem``, ``-iquote``, ``-idirafter``, joined or
  separate.
- MSVC ``cl`` and ``clang-cl``: also ``/I``, ``/external:I``, ``-external:I``,
  ``-imsvc``, and ``clang-cl`` ``/clang:`` pass-through options. Slash options
  are read only for an MSVC-style command, since on a Unix command line
  ``/Include/x.cpp`` is a path, not ``/I``.

A ``command`` string is split the way its own platform splits it: POSIX
``shlex`` for a Unix command line, the Windows argv rules
(``CommandLineToArgvW``) for an MSVC driver or a command holding a
drive-letter path, because POSIX splitting drops the backslashes
(``-IC:\\src`` became ``-IC:src``). ``@file`` response files are expanded
relative to the entry's ``directory``.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

_MSVC_DRIVERS = frozenset({"cl", "clang-cl"})
# A drive-letter path starting a token, optionally behind an option (-IC:\x,
# /I"C:\x"); never one inside a value: -DFMT="a:\n" is a Unix command.
_DRIVE_PATH = re.compile(r'(?:^|\s)"?(?:[-/][A-Za-z:]*"?)?[A-Za-z]:\\')
# Longest prefix first: ``-isystem`` must not be read as ``-I`` + ``system``.
_MSVC_INCLUDE_OPTS = ("/external:I", "/imsvc", "/I")
_INCLUDE_OPTS = ("-external:I", "-idirafter", "-isystem", "-iquote", "-imsvc", "-I")
# Nested response files deeper than this are a cycle or a broken build.
_MAX_RSP_DEPTH = 8


def _driver_stem(token: str) -> str:
    name = re.split(r"[\\/]", token)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def split_windows(command: str) -> list[str]:
    """Split *command* by the ``CommandLineToArgvW`` rules ``cl.exe`` uses."""
    args: list[str] = []
    buf: list[str] = []
    in_quotes = False
    pending = False  # an argument has started, even if it is still empty ("")
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if c == "\\":
            j = i
            while j < n and command[j] == "\\":
                j += 1
            count = j - i
            if j < n and command[j] == '"':
                # 2k backslashes + quote: k backslashes, the quote toggles;
                # 2k+1: k backslashes and a literal quote.
                buf.append("\\" * (count // 2))
                if count % 2:
                    buf.append('"')
                    j += 1
            else:
                buf.append("\\" * count)
            i, pending = j, True
        elif c == '"':
            if in_quotes and i + 1 < n and command[i + 1] == '"':
                buf.append('"')
                i += 2
            else:
                in_quotes = not in_quotes
                i += 1
            pending = True
        elif c in " \t\r\n" and not in_quotes:  # newlines separate response-file lines
            if pending:
                args.append("".join(buf))
                buf, pending = [], False
            i += 1
        else:
            buf.append(c)
            i, pending = i + 1, True
    if pending:
        args.append("".join(buf))
    return args


def split_command(command: str, *, windows: bool = False) -> list[str]:
    """Split a database ``command`` string; raises ``ValueError`` on bad quoting."""
    if windows or _DRIVE_PATH.search(command) or _has_msvc_driver(split_windows(command)):
        return split_windows(command)
    return shlex.split(command)


def _has_msvc_driver(tokens: list[str]) -> bool:
    """``cl``/``clang-cl`` is the driver, directly or behind one wrapper (a compiler cache)."""
    return any(_driver_stem(t) in _MSVC_DRIVERS for t in tokens[:2])


def is_msvc_style(tokens: list[str]) -> bool:
    """True for an MSVC-style command line: a cl driver, or a ``.exe`` driver given ``/c``."""
    if _has_msvc_driver(tokens):
        return True
    return bool(tokens) and tokens[0].lower().endswith(".exe") and "/c" in tokens


def _read_response_file(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    # MSBuild and the IDE write response files as UTF-16, usually with a BOM;
    # without one, a NUL second byte gives UTF-16LE text away.
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    if raw[1:2] == b"\x00":
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


def expand_response_files(
    tokens: list[str], base_dir: Path, *, windows: bool, _seen: frozenset[Path] = frozenset()
) -> list[str]:
    """Replace each ``@file`` token with the arguments the file holds.

    A missing or unreadable file, or one already being expanded (a cycle),
    is dropped: the compiler would fail on it, and its options are unknown.
    """
    out: list[str] = []
    for tok in tokens:
        if not (tok.startswith("@") and len(tok) > 1):
            out.append(tok)
            continue
        path = Path(tok[1:])
        if not path.is_absolute():
            path = base_dir / path
        path = Path(os.path.normpath(path))
        if path in _seen or len(_seen) >= _MAX_RSP_DEPTH:
            continue
        text = _read_response_file(path)
        if text is None:
            continue
        try:
            inner = split_command(text, windows=windows)
        except ValueError:
            continue
        out.extend(expand_response_files(inner, base_dir, windows=windows, _seen=_seen | {path}))
    return out


def include_dirs_from_tokens(tokens: list[str]) -> list[str]:
    """Include directories named by *tokens*, as written (possibly relative)."""
    msvc = is_msvc_style(tokens)
    opts = (_MSVC_INCLUDE_OPTS + _INCLUDE_OPTS) if msvc else _INCLUDE_OPTS
    dirs: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if msvc and tok.startswith("/clang:"):
            tok = tok[len("/clang:") :]
        for opt in opts:
            if tok == opt:
                if i + 1 < len(tokens):
                    nxt = tokens[i + 1]
                    if msvc and nxt.startswith("/clang:"):
                        nxt = nxt[len("/clang:") :]
                    dirs.append(nxt)
                    i += 1
                break
            if tok.startswith(opt):
                dirs.append(tok[len(opt) :])
                break
        i += 1
    return dirs


def entry_tokens(entry: dict, base_dir: Path) -> list[str]:
    """The argument list of one database *entry*, response files expanded."""
    if "arguments" in entry:
        tokens = [str(t) for t in entry["arguments"]]
        windows = is_msvc_style(tokens)
    else:
        command = str(entry.get("command", ""))
        try:
            tokens = split_command(command)
        except ValueError:
            return []
        windows = tokens != [] and (is_msvc_style(tokens) or bool(_DRIVE_PATH.search(command)))
    if any(t.startswith("@") for t in tokens):
        tokens = expand_response_files(tokens, base_dir, windows=windows)
    return tokens
