"""Explicit LPC routing and conservative literal import resolution.

LPC and C both use .c/.h; only explicitly configured mudlib roots opt in.
No preprocessor is simulated: imports containing macro names stay unresolved.
"""

from __future__ import annotations

import importlib.util
import json
import os
import posixpath
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

LPC_EXTENSIONS = frozenset({".c", ".h"})
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')
_INHERIT = re.compile(r"^(?:(?:private|protected|public|virtual|functions|variables)\s+)*inherit\s+(.+?)\s*;\s*$", re.S)
_INCLUDE = re.compile(r"^#\s*include\s+(.+?)\s*$", re.S)


@dataclass(frozen=True)
class LpcConfig:
    roots: tuple[Path, ...] = ()
    include: tuple[str, ...] = ("",)


def _string_list(data: dict, name: str, default: list[str]) -> list[str]:
    value = data.get(name, default)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"LPC config {name!r} must be an array of strings")
    return value


@lru_cache(maxsize=4)
def _read_config(path: Path, mtime_ns: int, size: int) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("LPC config must be a JSON object")
    return data


def config() -> LpcConfig:
    """Read explicit roots and include directories; invalidate on config file changes."""
    config_path = os.environ.get("REPOWISE_LPC_CONFIG", "").strip()
    data: dict = {}
    base = Path.cwd()
    if config_path:
        path = Path(config_path).expanduser().resolve()
        stat = path.stat()
        data = _read_config(path, stat.st_mtime_ns, stat.st_size)
        base = path.parent
    roots = [
        (base / Path(item).expanduser()).resolve()
        for item in _string_list(data, "roots", [])
    ]
    roots.extend(
        Path(item).expanduser().resolve()
        for item in os.environ.get("REPOWISE_LPC_ROOTS", "").split(os.pathsep)
        if item.strip()
    )
    return LpcConfig(
        roots=tuple(sorted(set(roots), key=lambda p: (-len(p.parts), p.as_posix()))),
        include=tuple(_string_list(data, "include", [""])),
    )


def root_for(path: Path | str, cfg: LpcConfig | None = None) -> Path | None:
    """Return the most specific configured mudlib root containing a physical path."""
    roots = (cfg or config()).roots
    if not roots:
        return None
    resolved = Path(path).resolve()
    for root in roots:
        if resolved.is_relative_to(root):
            return root
    return None


@lru_cache(maxsize=1)
def grammar_available() -> bool:
    """True when the optional ``tree-sitter-lpc`` grammar (``repowise[lpc]``) is installed."""
    return importlib.util.find_spec("tree_sitter_lpc") is not None


def is_lpc_path(path: Path | str) -> bool:
    """Opt .c/.h files into LPC only inside explicitly configured roots.

    Without the grammar LPC stays inactive and the files keep their C/C++ classification.
    """
    if Path(path).suffix.lower() not in LPC_EXTENSIONS or root_for(path) is None:
        return False
    if grammar_available():
        return True
    _warn_grammar_missing()
    return False


@lru_cache(maxsize=1)
def _warn_grammar_missing() -> None:
    log.warning(
        "LPC roots configured but the grammar is not installed; .c/.h stay C/C++",
        fix='pip install "repowise[lpc]"',
    )


def lpc_call_target(text: str, node_type: str) -> str:
    """Normalize named callback strings and lfun closures, excluding operators."""
    if node_type == "string_literal":
        text = text[1:-1] if text.startswith('"') and text.endswith('"') else ""
    elif node_type == "closure":
        text = text[2:] if text.startswith("#'") else ""
        if text.startswith("::"):
            return ""  # inherited closure dispatch is not modeled yet
    return text if _IDENTIFIER.fullmatch(text) else ""


def _literal(expr: str) -> str | None:
    """One literal path; macros, escapes and expressions are intentionally unsupported."""
    expr = expr.strip()
    if not _STRING.fullmatch(expr) or "\\" in expr:
        return None
    return expr[1:-1]


def _existing(root: Path, name: str) -> Path | None:
    relative = posixpath.normpath(name.lstrip("/"))
    if relative == ".." or relative.startswith("../"):
        return None
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        return None
    return candidate if candidate.is_file() else None


def _inherit_target(expression: str, root: Path) -> Path | None:
    name = _literal(expression)
    if not name or not name.startswith("/"):
        return None
    names = [name] if name.endswith(".c") else [name + ".c", name]
    for candidate in names:
        if hit := _existing(root, candidate):
            return hit
    return None


def _include_target(operand: str, importer: Path, root: Path, cfg: LpcConfig) -> Path | None:
    operand = operand.strip()
    system = operand.startswith("<") and operand.endswith(">")
    name = operand[1:-1] if system else _literal(operand)
    if not name:
        return None
    if name.startswith("/"):
        return _existing(root, name)
    directories = list(cfg.include)
    if not system:
        directories.insert(0, importer.parent.relative_to(root).as_posix())
    for directory in directories:
        if hit := _existing(root, posixpath.join(directory, name)):
            return hit
    return None


def resolve_statement(statement: str, importer: Path) -> Path | None:
    """Resolve literal inherit/include paths within the importer's own mudlib.

    Absolute LPC paths are relative to the mudlib root, not the host root.
    Relative inherits are deliberately unresolved: their interpretation depends
    on driver mode and master hooks that are not configured here.
    """
    cfg = config()
    importer = importer.resolve()
    root = root_for(importer, cfg)
    if root is None:
        return None
    text = statement.strip()
    if match := _INHERIT.fullmatch(text):
        return _inherit_target(match[1], root)
    if match := _INCLUDE.fullmatch(text):
        return _include_target(match[1], importer, root, cfg)
    return None
