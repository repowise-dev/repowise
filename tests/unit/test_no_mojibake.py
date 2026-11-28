"""No UTF-8 text double-encoded through cp1252 in tracked files (#4090).

Fails with ``path:line`` when any tracked UTF-8 text file contains a run of
characters that round-trips through cp1252 back to a single UTF-8 character
(for example an em dash saved as ``\\u00e2\\u20ac\\u201d``).
"""

from __future__ import annotations

import pathlib
import re
import subprocess

_ROOT = pathlib.Path(__file__).resolve().parents[2]

_RUN = re.compile(
    r"[\u00c2-\u00f4][\u0080-\u00bf\u0152\u0153\u0160\u0161\u0178\u017d\u017e"
    r"\u0192\u02c6\u02dc\u2013\u2014\u2018-\u201e\u2020-\u2022\u2026\u2030"
    r"\u2039\u203a\u20ac\u2122]+"
)


def _mojibake_hits_in_text(text: str) -> list[tuple[int, str, str]]:
    hits: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in _RUN.finditer(line):
            run = match.group()
            try:
                decoded = run.encode("cp1252").decode("utf-8")
            except UnicodeError:
                continue
            if len(decoded) == 1:
                hits.append((lineno, run, decoded))
    return hits


def _tracked_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=_ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return proc.stdout.splitlines()


def _read_utf8_text(path: pathlib.Path) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def test_no_cp1252_mojibake_in_tracked_files() -> None:
    offenders: list[str] = []
    for rel in _tracked_files():
        text = _read_utf8_text(_ROOT / rel)
        if text is None:
            continue
        for lineno, run, decoded in _mojibake_hits_in_text(text):
            offenders.append(f"{rel}:{lineno}: {run!r} -> {decoded!r}")

    assert not offenders, (
        "Double-encoded UTF-8 (cp1252 mojibake) found in tracked files:\n"
        + "\n".join(f"  {entry}" for entry in offenders)
    )


def test_detector_identifies_round_trip_mojibake() -> None:
    garbled_dash = "Update everything \u2014 only affected".encode().decode("cp1252")
    garbled_cafe = "caf\u00e9".encode().decode("cp1252")
    garbled_emoji = "ship \U0001f680 now".encode().decode("cp1252")
    clean = "Update everything \u2014 only affected\ncaf\u00e9\nship \U0001f680 now\n"

    assert _mojibake_hits_in_text(f"{garbled_dash}\n{garbled_cafe}\n{garbled_emoji}\n") == [
        (1, "\u2014".encode().decode("cp1252"), "\u2014"),
        (2, "\u00e9".encode().decode("cp1252"), "\u00e9"),
        (3, "\U0001f680".encode().decode("cp1252"), "\U0001f680"),
    ]
    assert _mojibake_hits_in_text(clean) == []

