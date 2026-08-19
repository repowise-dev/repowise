"""Data-driven output filters loaded from TOML definitions.

A whole new command filter becomes a ``.toml`` data file with inline tests,
not a Python module plus router/config/doctor edits. One generic applier
(:class:`TomlFilter`) interprets every definition, so the count of supported
commands scales with data, not code.

The one guarantee the applier adds on top of the raw declaration is our
cardinal invariant: a ``strip_lines_matching`` / ``keep_lines_matching`` / cap
can never drop a line :func:`is_error_line` classifies as a failure. A filter
author literally cannot author an error line away, and a ``match_output``
short-circuit only fires on fully error-free output. That keeps the
errors-first contract independent of who wrote the filter, which is what makes
accepting data-authored definitions safe.

Supported fields (proven sufficient for the built-in set):

``description``, ``priority``, ``min_lines``, ``match_command``,
``match_content``, ``strip_ansi``, ``strip_lines_matching``,
``keep_lines_matching``, ``replace``, ``match_output`` (short-circuit, gated on
error-free output), ``truncate_lines_at``, ``max_lines``, ``tail_lines``,
``on_empty``.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import structlog

from repowise.core.distill.filters.base import OutputFilter, cap_block, is_error_line
from repowise.core.distill.registry import filter_registry

logger = structlog.get_logger(__name__)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

#: Directory of built-in TOML filter definitions (one file = one filter family).
_BUILTIN_DIR = Path(__file__).parent / "filters_toml"


class TomlFilter(OutputFilter):
    """One filter defined entirely by a TOML mapping.

    Not registered via the class decorator (there is one class, many
    instances); :func:`load_toml_filters` builds one instance per definition
    and registers it directly with :meth:`FilterRegistry.register_instance`.
    """

    def __init__(self, spec: dict[str, Any], *, origin: str = "<builtin>") -> None:
        self.name = str(spec["name"])
        self.origin = origin
        self.priority = int(spec.get("priority", 50))
        self.min_lines = int(spec.get("min_lines", 8))
        self.description = str(spec.get("description", ""))
        self._strip_ansi = bool(spec.get("strip_ansi", False))
        self._cmd_re = _opt_re(spec.get("match_command"))
        self._content_re = _opt_re(spec.get("match_content"))
        self._strip = [re.compile(p) for p in spec.get("strip_lines_matching", [])]
        self._keep = [re.compile(p) for p in spec.get("keep_lines_matching", [])]
        self._replace = [
            (re.compile(r["pattern"]), r["replacement"]) for r in spec.get("replace", [])
        ]
        self._short_circuit = [
            (re.compile(r["pattern"]), r["message"]) for r in spec.get("match_output", [])
        ]
        self._truncate_at = spec.get("truncate_lines_at")
        self._max_lines = spec.get("max_lines")
        self._tail_lines = spec.get("tail_lines")
        self._on_empty = spec.get("on_empty")

    # -- routing ---------------------------------------------------------------

    def matches_command(self, command: str) -> bool:
        return bool(self._cmd_re and self._cmd_re.search(command))

    def matches_content(self, output: str) -> bool:
        return bool(self._content_re and self._content_re.search(output))

    # -- distillation ----------------------------------------------------------

    def distill(self, output: str, *, command: str = "", exit_code: int = 0) -> str:
        text = _ANSI_RE.sub("", output) if self._strip_ansi else output
        lines = text.splitlines()

        short = self._try_short_circuit(lines)
        if short is not None:
            return short

        kept = self._apply_caps(self._filter_lines(lines))

        if not kept:
            if self._on_empty is not None:
                return str(self._on_empty)
            raise ValueError(f"{self.name}: nothing left after filtering")

        return self._render(kept, lines)

    def _try_short_circuit(self, lines: list[str]) -> str | None:
        """Collapse to a one-line summary when there is nothing to preserve.

        Gated on the whole output being error-free: a short-circuit that
        swallowed an error line would violate the errors-first invariant.
        """
        if not self._short_circuit or any(is_error_line(ln) for ln in lines):
            return None
        for pat, message in self._short_circuit:
            if any(pat.search(ln) for ln in lines):
                return message
        return None

    def _filter_lines(self, lines: list[str]) -> list[str]:
        """Keep/strip, then replace and truncate — never touching error lines."""
        kept: list[str] = []
        for ln in lines:
            error = is_error_line(ln)
            # Invariant: an error line is always kept, whatever the rules say.
            if not self._keep_line(ln) and not error:
                continue
            kept.append(ln if error else self._rewrite_line(ln))
        return kept

    def _rewrite_line(self, line: str) -> str:
        """Apply replacements + truncation to a non-error line.

        Callers guarantee *line* is not an error line, so a substitution or a
        truncation can never mangle the one line that explains a failure.
        """
        out = line
        for pat, repl in self._replace:
            out = pat.sub(repl, out)
        if self._truncate_at and len(out) > int(self._truncate_at):
            out = out[: int(self._truncate_at) - 1] + "…"
        return out

    def _render(self, kept: list[str], original: list[str]) -> str:
        """Join *kept*, re-appending any error line the caps elided.

        Belt-and-braces rescue tail, identical in spirit to the hand-written
        filters: the caps preserve error lines already, but a re-scan is cheap
        insurance against a future cap that forgets to.
        """
        rendered = "\n".join(kept)
        rescued = [ln for ln in original if ln.strip() and is_error_line(ln) and ln not in rendered]
        if rescued:
            kept = [*kept, *rescued]
        return "\n".join(kept)

    def _keep_line(self, line: str) -> bool:
        if self._keep:
            return any(p.search(line) for p in self._keep)
        if self._strip:
            return not any(p.search(line) for p in self._strip)
        return True

    def _apply_caps(self, lines: list[str]) -> list[str]:
        if self._max_lines and self._tail_lines:
            return cap_block(lines, int(self._max_lines), int(self._tail_lines))
        if self._max_lines:
            return cap_block(lines, int(self._max_lines), 0)
        if self._tail_lines:
            return cap_block(lines, 0, int(self._tail_lines))
        return lines


def _opt_re(pattern: Any) -> re.Pattern[str] | None:
    return re.compile(str(pattern)) if pattern else None


def parse_toml_filters(path: Path) -> list[TomlFilter]:
    """Build the :class:`TomlFilter` instances declared in one ``.toml`` file.

    Best-effort: a malformed file or a single bad definition is logged and
    skipped, never fatal — one broken filter must not disable the engine.
    Registration is left to the caller so this stays a pure parse.
    """
    filters: list[TomlFilter] = []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("distill: skipping unreadable TOML filter", path=str(path), error=str(exc))
        return filters
    for name, spec in (data.get("filters") or {}).items():
        try:
            filters.append(TomlFilter({"name": name, **spec}, origin=path.name))
        except (re.error, KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "distill: skipping invalid TOML filter",
                path=str(path),
                filter=name,
                error=str(exc),
            )
    return filters


def load_toml_filters(directory: Path | None = None) -> list[TomlFilter]:
    """Parse every ``*.toml`` in *directory* and register the filters.

    Files are loaded in sorted order for a stable content-sniff tie-break.
    Returns the filters that registered, for callers (tests, doctor) that want
    to inspect what loaded.
    """
    directory = directory or _BUILTIN_DIR
    loaded: list[TomlFilter] = []
    if not directory.is_dir():
        return loaded
    for path in sorted(directory.glob("*.toml")):
        for f in parse_toml_filters(path):
            filter_registry.register_instance(f)
            loaded.append(f)
    return loaded
