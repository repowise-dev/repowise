"""Vite ``resolve.alias`` entries, read from ``vite.config.*`` as text.

A Vite app with no tsconfig/jsconfig ``paths`` declares its import aliases
only in its Vite config (``vitest.config.*`` takes the same key), so without
this every ``@/...`` import read as a package and the files behind it as
unreachable. :class:`~.tsconfig_resolver.TsconfigResolver` turns these entries
into alias patterns and tries them after the tsconfig ones.

Vite matches a string ``find`` when the specifier equals it or continues it
with ``/``, and the first entry that matches decides. Both the object form
(``alias: {"@": X}``) and the array form (``alias: [{find: "@",
replacement: X}]``) are read. A replacement is read when it is one of:

* a string literal starting with ``/`` (the Vite root);
* ``path.resolve`` / ``path.join`` over ``__dirname``, ``import.meta.dirname``
  or ``process.cwd()`` and string literals;
* ``fileURLToPath(new URL("./src", import.meta.url))`` or
  ``new URL("./src", import.meta.url).pathname``;
* ``__dirname + "/src"`` and `` `${__dirname}/src` `` (also with
  ``import.meta.dirname``).

Any other replacement still claims its ``find`` (target ``None``), so a later
entry never answers for a specifier Vite would hand to an earlier one.

Ceiling: the config is never evaluated. A regex ``find``, a replacement built
from a variable or a helper call, a ``./`` literal (Vite keeps it relative to
the importer), a bare one (a package redirect) and aliases spread in from
another module are not followed, so those imports keep today's behaviour (no
edge). ``process.cwd()``, a base-less ``path.resolve`` and a ``/``-rooted
literal are read against the config's directory, where Vite is normally run
from and its default ``root``; a config that sets another ``root`` is not
followed.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

from .languages.js_config_values import split_top_level, value_spans

# The names Vite and Vitest look for by default; a ``vite.config.backup.ts``
# is only loaded through ``--config``.
_CONFIG_NAMES = frozenset(
    f"{stem}.config.{ext}"
    for stem in ("vite", "vitest")
    for ext in ("js", "mjs", "ts", "cjs", "mts", "cts")
)

#: Globs that find the configs :func:`is_vite_config` accepts.
VITE_CONFIG_GLOBS = ("vite.config.*", "vitest.config.*")

# One string literal with no escapes and no template hole.
_LIT = r"""(?:'([^'\\\n]*)'|"([^"\\\n]*)"|`([^`\\$]*)`)"""
_LIT_RE = re.compile(rf"^{_LIT}$")
# What the config's own directory is spelled as.
_DIR = r"(?:__dirname|import\.meta\.dirname|process\.cwd\(\s*\))"
_DIR_RE = re.compile(rf"^{_DIR}$")
_CONCAT_RE = re.compile(rf"^{_DIR}\s*\+\s*{_LIT}$")
_TEMPLATE_RE = re.compile(rf"^`\$\{{\s*{_DIR}\s*\}}([^`\\$]*)`$")
_URL = rf"new\s+URL\(\s*{_LIT}\s*,\s*import\.meta\.url\s*\)"
_FILE_URL_RE = re.compile(rf"^(?:[A-Za-z_$][\w$]*\.)?fileURLToPath\(\s*{_URL}\s*\)$")
_URL_PATHNAME_RE = re.compile(rf"^{_URL}\.pathname$")
# ``resolve(...)`` / ``join(...)`` imported from ``path``, or called on a
# module whose name says it is one (``path``, ``nodePath``, ``pathe``).
_PATH_CALL_RE = re.compile(r"^(?:([A-Za-z_$][\w$]*)\.)?(resolve|join)\((.*)\)$", re.DOTALL)
_IDENT_RE = re.compile(r"^[A-Za-z_$][\w$]*")


def is_vite_config(name: str) -> bool:
    """Whether the file *name* is one Vite or Vitest loads by default."""
    return name in _CONFIG_NAMES


def vite_aliases(text: bytes, config_dir: Path) -> list[tuple[str, Path | None]]:
    """``(find, replacement path)`` for each string ``find`` in a Vite config, in order.

    The path is ``None`` where the replacement is not read statically.
    """
    out: list[tuple[str, Path | None]] = []
    for span in value_spans(text, ("alias",)):
        span = span.strip()
        if span.startswith(b"{") and span.endswith(b"}"):
            # A repeated key keeps its first position and its last value, as in JS.
            pairs = list(dict(_object_entries(span)).items())
        elif span.startswith(b"[") and span.endswith(b"]"):
            pairs = []
            for element in split_top_level(span[1:-1]):
                element = element.strip()
                if not (element.startswith(b"{") and element.endswith(b"}")):
                    continue
                fields = dict(_object_entries(element))
                find = _literal(fields.get("find", ""))
                if find is not None:
                    pairs.append((find, fields.get("replacement", "")))
        else:
            continue
        for find, value in pairs:
            if not find:
                continue
            target, trailing_slash = _target(value, config_dir)
            # Vite drops a trailing ``/`` from both sides when both carry one.
            if find.endswith("/") and trailing_slash and len(find) > 1:
                find = find[:-1]
            out.append((find, target))
    return out


def _object_entries(obj: bytes) -> list[tuple[str, str]]:
    """``(key, value expression)`` for each plain ``key: value`` in an object literal.

    Spreads, computed keys and shorthand properties are skipped.
    """
    entries: list[tuple[str, str]] = []
    for part in split_top_level(obj[1:-1]):
        text = part.decode("utf-8", errors="replace").strip()
        if not text or text.startswith("..."):
            continue
        if text[0] in "'\"":
            end = text.find(text[0], 1)
            if end < 0:
                continue
            key, rest = text[1:end], text[end + 1 :]
        elif (m := _IDENT_RE.match(text)) is not None:
            key, rest = m.group(0), text[m.end() :]
        else:
            continue
        rest = rest.lstrip()
        if rest.startswith(":"):
            entries.append((key, rest[1:].strip()))
    return entries


def _literal(expr: str) -> str | None:
    m = _LIT_RE.match(expr.strip())
    return None if m is None else _first(m)


def _first(m: re.Match[str]) -> str:
    return next(g for g in m.groups() if g is not None)


def _target(expr: str, config_dir: Path) -> tuple[Path | None, bool]:
    """The directory or file a replacement names (or ``None``), and whether it ends in ``/``."""
    expr = expr.strip()
    lit = _literal(expr)
    if lit is not None:
        if lit.startswith("/"):
            return config_dir / lit.lstrip("/"), lit.endswith("/")
        return None, False
    m = _CONCAT_RE.match(expr) or _TEMPLATE_RE.match(expr)
    if m is not None:
        rest = _first(m)
        # ``__dirname + "src"`` glues onto the directory name itself.
        if not rest.startswith("/"):
            return None, False
        return config_dir / rest.lstrip("/"), rest.endswith("/")
    m = _FILE_URL_RE.match(expr) or _URL_PATHNAME_RE.match(expr)
    if m is not None:
        rel = _first(m)
        if rel.startswith("/"):
            return None, False
        return config_dir / unquote(rel), rel.endswith("/")
    m = _PATH_CALL_RE.match(expr)
    if m is not None and (m.group(1) is None or "path" in m.group(1).lower()):
        return _path_call(m.group(2), m.group(3), config_dir)
    return None, False


def _path_call(func: str, args: str, config_dir: Path) -> tuple[Path | None, bool]:
    """``path.resolve``/``path.join`` over a directory and literals.

    ``resolve`` restarts at an absolute segment (leaving the repository, so
    ``None``) and drops a trailing ``/``; ``join`` keeps both under the base.
    """
    texts = [p.decode("utf-8", errors="replace").strip() for p in split_top_level(args.encode())]
    texts = [t for t in texts if t]
    if not texts:
        return None, False
    if _DIR_RE.match(texts[0]):
        texts = texts[1:]
    result = config_dir
    segment = ""
    for text in texts:
        segment = _literal(text)
        if segment is None or (func == "resolve" and segment.startswith("/")):
            return None, False
        result = result / segment.lstrip("/")
    return result, func == "join" and segment.endswith("/")
