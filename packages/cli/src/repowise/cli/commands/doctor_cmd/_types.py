"""Shared value types and tiny formatting helpers for ``repowise doctor``."""

from __future__ import annotations

from typing import NamedTuple


class DoctorCheck(NamedTuple):
    name: str
    ok: bool
    detail: str = ""


def _check(name: str, ok: bool, detail: str = "") -> DoctorCheck:
    return DoctorCheck(name, ok, detail)


def _status_markup(ok: bool) -> str:
    return "[green]OK[/green]" if ok else "[red]FAIL[/red]"
