"""The doctor row type, its ok/warn/fail level, and the level's table markup."""

from __future__ import annotations

from typing import Literal, NamedTuple

from repowise.cli.ui.brand import ERR, OK, WARN
from repowise.cli.ui.glyphs import glyph

Level = Literal["ok", "warn", "fail"]


class DoctorCheck(NamedTuple):
    """One doctor row.

    ``id`` is the stable machine key (lowercase kebab); ``name`` is display
    text and may change. ``ok`` is False only when ``level`` is ``fail``, so
    a ``warn`` row never fails the run. ``fix`` is the exact command that
    resolves the row, when one is known. ``data`` carries a row's facts under
    stable keys for an agent reading the JSON, when the row has any. Build
    rows with :func:`_check`.
    """

    name: str
    ok: bool
    detail: str = ""
    id: str = ""
    level: Level = "ok"
    fix: str | None = None
    data: dict | None = None

    def as_json(self) -> dict:
        """The row for ``--format json``; ``data`` only on a row that has facts."""
        row = self._asdict()
        if row["data"] is None:
            del row["data"]
        return row


def _check(
    name: str,
    ok: bool,
    detail: str = "",
    *,
    check_id: str,
    warn: bool = False,
    fix: str | None = None,
    data: dict | None = None,
) -> DoctorCheck:
    """A row; *warn* marks a passing row the user should still look at."""
    level: Level = "fail" if not ok else ("warn" if warn else "ok")
    return DoctorCheck(name, ok, detail, check_id, level, fix, data)


_LEVEL_MARKUP = {
    "ok": (OK, "ok", "OK"),
    "warn": (WARN, "warn", "WARN"),
    "fail": (ERR, "fail", "FAIL"),
}


def _status_markup(level: Level) -> str:
    style, glyph_name, label = _LEVEL_MARKUP[level]
    return f"[{style}]{glyph(glyph_name)} {label}[/{style}]"
