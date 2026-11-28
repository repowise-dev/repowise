"""`repowise serve` opens the web UI in a browser only when a person can see it."""

from __future__ import annotations

import threading

import pytest

from repowise.cli.commands.serve_cmd import _browser

_OPEN = {"ui_started": True, "no_open": False, "interactive": True, "ci": False, "has_display": True}


def test_opens_for_an_interactive_run_whose_ui_started() -> None:
    assert _browser.should_open_browser(**_OPEN)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ui_started", False),  # --no-ui, or no Node / failed download
        ("no_open", True),
        ("interactive", False),
        ("ci", True),
        ("has_display", False),
    ],
)
def test_any_blocker_keeps_the_browser_closed(field: str, value: bool) -> None:
    assert not _browser.should_open_browser(**{**_OPEN, field: value})


def test_opens_the_ui_root_once_after_it_answers() -> None:
    answers = iter([False, False, True])
    probed: list[str] = []
    opened: list[str] = []

    def probe(url: str) -> bool:
        probed.append(url)
        return next(answers)

    def opener(url: str) -> bool:
        opened.append(url)
        return True

    assert _browser._wait_and_open(
        "http://localhost:3000", None, probe=probe, opener=opener, interval_s=0
    )
    assert len(probed) == 3
    assert opened == ["http://localhost:3000"]


def test_never_opens_a_url_that_is_not_up() -> None:
    opened: list[str] = []
    result = _browser._wait_and_open(
        "http://localhost:3000",
        None,
        probe=lambda _url: False,
        opener=opened.append,
        timeout_s=0,
        interval_s=0,
    )
    assert result is False
    assert opened == []


def test_stops_waiting_when_the_ui_process_exits() -> None:
    class _Exited:
        def poll(self) -> int:
            return 1

    opened: list[str] = []
    result = _browser._wait_and_open(
        "http://localhost:3000",
        _Exited(),  # type: ignore[arg-type]
        probe=lambda _url: False,
        opener=opened.append,
        timeout_s=60,
        interval_s=0,
    )
    assert result is False
    assert opened == []


def test_an_opener_failure_is_swallowed() -> None:
    def boom(_url: str) -> bool:
        raise RuntimeError("no browser")

    assert not _browser._wait_and_open(
        "http://localhost:3000", None, probe=lambda _url: True, opener=boom
    )


def test_open_when_ready_targets_localhost_on_the_ui_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    done = threading.Event()
    opened: list[str] = []

    def fake_open(url: str) -> bool:
        opened.append(url)
        done.set()
        return True

    monkeypatch.setattr(_browser, "_ui_answers", lambda _url: True)
    monkeypatch.setattr(_browser.webbrowser, "open", fake_open)
    _browser.open_when_ready(3001, None)
    assert done.wait(5)
    assert opened == ["http://localhost:3001"]


def test_ui_answers_counts_an_error_status_as_up(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    def raise_http_error(url: str, timeout: float) -> None:
        raise urllib.error.HTTPError(url, 500, "boom", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr(_browser._NO_PROXY_OPENER, "open", raise_http_error)
    assert _browser._ui_answers("http://localhost:3000")


def test_ui_answers_is_false_when_nothing_listens(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(url: str, timeout: float) -> None:
        raise ConnectionRefusedError

    monkeypatch.setattr(_browser._NO_PROXY_OPENER, "open", refuse)
    assert not _browser._ui_answers("http://localhost:3000")


@pytest.mark.parametrize(("no_open", "expected"), [(False, [(3000, "proc")]), (True, [])])
def test_serve_wires_the_flag_and_terminal_into_the_decision(
    monkeypatch: pytest.MonkeyPatch, no_open: bool, expected: list[tuple[int, str]]
) -> None:
    import io
    import sys

    from repowise.cli.commands.serve_cmd import command
    from repowise.cli.platform.telemetry import environment

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    calls: list[tuple[int, str]] = []
    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr(sys, "stdout", _Tty())
    monkeypatch.setattr(environment, "is_ci", lambda: False)
    monkeypatch.setattr(_browser, "has_display_for_browser", lambda: True)
    monkeypatch.setattr(_browser, "open_when_ready", lambda port, proc: calls.append((port, proc)))
    command._maybe_open_browser("proc", 3000, no_open=no_open)  # type: ignore[arg-type]
    assert calls == expected


@pytest.mark.parametrize(("env", "args", "expected"), [(None, [], False), ("1", [], True), (None, ["--no-open"], True)])
def test_no_open_reads_the_flag_and_env(
    monkeypatch: pytest.MonkeyPatch, env: str | None, args: list[str], expected: bool
) -> None:
    from repowise.cli.commands.serve_cmd import serve_command

    if env is None:
        monkeypatch.delenv("REPOWISE_NO_OPEN", raising=False)
    else:
        monkeypatch.setenv("REPOWISE_NO_OPEN", env)
    ctx = serve_command.make_context("serve", list(args))
    assert ctx.params["no_open"] is expected
