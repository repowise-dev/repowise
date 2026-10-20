"""The init theme: style names on the console, glyphs with an ASCII form, the owl's states."""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from repowise.cli import _stdio
from repowise.cli._helpers.console import _CliConsole
from repowise.cli.commands.init_cmd._finish import _finish_owl
from repowise.cli.ui import checklist, glyphs, mascot
from repowise.cli.ui.brand import THEME
from repowise.cli.ui.result_panels import build_completion_panel

_CLI = Path(__file__).resolve().parents[3] / "packages" / "cli" / "src" / "repowise" / "cli"


@pytest.fixture
def terminal_encoding(monkeypatch: pytest.MonkeyPatch):
    """Set what stdout could carry before the CLI's UTF-8 reconfigure."""

    def _set(encoding: str) -> None:
        monkeypatch.setattr(_stdio, "_original_stdout_encoding", encoding)

    return _set


def test_a_cp1252_stdout_gets_the_ascii_forms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_stdio, "_original_stdout_encoding", None)
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    _stdio._ensure_utf8_stdio()

    # The stream now writes UTF-8; the encoding it started with still decides.
    assert stream.encoding == "utf-8"
    assert glyphs.ascii_only()
    assert glyphs.glyph("ok") == "+"
    assert mascot.mini(mascot.IDLE) == "{o,o}"
    assert mascot.header_text("1.2.3").plain.isascii()

    console = _CliConsole(theme=THEME, width=80, force_terminal=False)
    console.print(build_completion_panel("repowise init complete", [("Files", "3 files")]))
    console.print(checklist.done_line(checklist.FinishedStep("  ↳ Resolving imports", 3, 3, "files", 0.2)))
    stream.flush()
    out = stream.buffer.getvalue().decode("utf-8")
    assert out.isascii(), out
    assert "{^,^}  repowise init complete" in out
    assert "+ Resolving imports - 3 files" in out


def test_a_utf8_stdout_gets_the_unicode_forms(terminal_encoding) -> None:
    terminal_encoding("utf-8")
    assert not glyphs.ascii_only()
    assert glyphs.glyph("ok") == "✓"
    assert mascot.mini(mascot.IDLE) == "{◉,◉}"
    assert checklist.running_label("  ↳ Resolving imports") == "  ↳ Resolving imports"


def test_every_glyph_has_a_one_line_ascii_form() -> None:
    for unicode, ascii_form in glyphs.GLYPHS.values():
        assert ascii_form.isascii()
        assert not unicode.isascii()


@pytest.mark.parametrize(
    ("state", "unicode", "ascii_form"),
    [
        (mascot.IDLE, "{◉,◉}", "{o,o}"),
        (mascot.BLINK, "{◒,◒}", "{o,o}"),
        (mascot.SHUT, "{─,─}", "{o,o}"),
        (mascot.DONE, "{^,^}", "{^,^}"),
        (mascot.INTERRUPTED, "{─,─}", "{-,-}"),
        (mascot.WARNED, "{•,•}", "{.,.}"),
        (mascot.FAILED, "{×,×}", "{x,x}"),
    ],
)
def test_owl_states(terminal_encoding, state: str, unicode: str, ascii_form: str) -> None:
    terminal_encoding("utf-8")
    assert mascot.mini(state) == unicode
    terminal_encoding("cp1252")
    assert mascot.mini(state) == ascii_form


def test_the_mini_owl_has_cream_strokes_and_orange_eyes() -> None:
    owl = mascot.mini_text(mascot.DONE)
    styles = [str(span.style) for span in owl.spans]
    assert styles == ["owl", "brand.bold", "owl", "brand.bold", "owl"]


def _run(*, embed_failed: int = 0, warnings: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(
        scope_embedder="openai",
        result=SimpleNamespace(embed_failed_pages=embed_failed),
        run_warnings=list(warnings),
        gaps=[],
    )


def test_the_finish_owl_follows_how_the_run_ended() -> None:
    assert _finish_owl(_run()) == mascot.DONE
    assert _finish_owl(_run(warnings=("git history skipped",))) == mascot.WARNED
    # An embed failure exits non-zero, so it outranks a warning.
    assert _finish_owl(_run(embed_failed=2, warnings=("x",))) == mascot.FAILED


def test_the_panel_title_carries_the_owl_state(terminal_encoding) -> None:
    terminal_encoding("utf-8")
    console = Console(file=io.StringIO(), width=80, theme=THEME)
    console.print(build_completion_panel("repowise init complete", [], owl=mascot.FAILED))
    assert "{×,×}  repowise init complete" in console.file.getvalue()


def test_the_theme_is_registered_on_the_cli_consoles() -> None:
    from repowise.cli.helpers import console, err_console

    for name in ("brand", "ok", "warn", "err", "info", "model", "dim", "value", "owl"):
        assert console.get_style(name) == THEME.styles[name]
        assert err_console.get_style(name) == THEME.styles[name]


_COLOUR_SGR = re.compile(r"\x1b\[[0-9;]*?(?:3[0-79]|38;[25];[0-9;]+)m")


def test_no_color_drops_colour_and_keeps_the_glyph(
    monkeypatch: pytest.MonkeyPatch, terminal_encoding
) -> None:
    terminal_encoding("utf-8")
    monkeypatch.setenv("NO_COLOR", "1")
    console = _CliConsole(theme=THEME, file=io.StringIO(), force_terminal=True, width=80)
    console.print(f"[ok]{glyphs.glyph('ok')}[/] done [warn]careful[/] [brand]repowise[/]")
    console.print(mascot.mini_text(mascot.WARNED))
    out = console.file.getvalue()
    assert not _COLOUR_SGR.search(out), repr(out)
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    assert "✓ done" in plain
    assert "{•,•}" in plain


# The init path names theme styles; the theme module is the one place a colour lives.
_INIT_PATH = [
    *sorted((_CLI / "commands" / "init_cmd").glob("*.py")),
    *(
        _CLI / "ui" / name
        for name in (
            "checklist.py",
            "glyphs.py",
            "mascot.py",
            "mode_selection.py",
            "progress.py",
            "progress_display.py",
            "result_panels.py",
        )
    ),
]
_RAW_COLOUR = re.compile(
    r"\[/?(?:bold |dim |italic )*(?:yellow|green|cyan|red|magenta)\]"
    r"|style=\"(?:bold )?(?:yellow|green|cyan|red|magenta)\""
    r"|#E8E8E8",
    re.IGNORECASE,
)


@pytest.mark.parametrize("path", _INIT_PATH, ids=lambda p: p.name)
def test_init_path_markup_names_no_raw_colour(path: Path) -> None:
    hits = [
        f"{path.name}:{number}: {line.strip()}"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _RAW_COLOUR.search(line)
    ]
    assert not hits, "use a theme style from ui/brand.py:\n" + "\n".join(hits)
