"""Terminal glyphs, each with an ASCII form for a stream that cannot show Unicode.

The choice follows stdout's encoding as the process found it (see
:func:`repowise.cli._stdio.original_stdout_encoding`): a UTF encoding gets the
Unicode glyph, anything else (cp1252, a non-UTF-8 pipe) gets ASCII. The CLI
reconfigures stdout to UTF-8 at import, so the live encoding always says UTF-8
and cannot be the signal.
"""

from __future__ import annotations

from repowise.cli import _stdio

#: name -> (unicode, ascii). Every ASCII form is one column wide unless the
#: Unicode one is not, so columns line up in both.
GLYPHS: dict[str, tuple[str, str]] = {
    "ok": ("✓", "+"),
    "fail": ("✗", "x"),
    "warn": ("⚠", "!"),
    "pending": ("○", "o"),
    "bullet": ("•", "*"),
    "substep": ("↳", ">"),
    "sep": ("·", "-"),
    "ellipsis": ("…", "..."),
    "arrow": ("→", "->"),
}

_TO_ASCII = str.maketrans({unicode: ascii_form for unicode, ascii_form in GLYPHS.values()})


def supports_unicode(encoding: str | None) -> bool:
    """True for a UTF encoding; no encoding at all (a StringIO) counts as UTF-8."""
    if not encoding:
        return True
    return encoding.lower().replace("-", "").replace("_", "").startswith("utf")


def ascii_only() -> bool:
    """Whether this process prints the ASCII forms."""
    return not supports_unicode(_stdio.original_stdout_encoding())


def glyph(name: str) -> str:
    """The glyph *name* in the form this terminal can show."""
    unicode, ascii_form = GLYPHS[name]
    return ascii_form if ascii_only() else unicode


def for_terminal(text: str) -> str:
    """*text* with every known glyph in this terminal's form.

    For text written elsewhere (pipeline messages, readout values), where the
    glyph is already baked into the string.
    """
    return text.translate(_TO_ASCII) if ascii_only() else text
