"""The anonymous ``init`` and ``generate`` outcome fields, and what they never carry.

``init`` says whether it ran the questions, which setup instructions started it
when the caller said, and whether a signed-in subscription CLI was found.
``generate`` says which kind of provider wrote, how the run ended, a bucket of
the pages written, and why a run that wrote nothing stopped. Every value is a
fixed label, a boolean or a bucket; none is a path, a name or free text.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click
import pytest

from repowise.cli.commands.generate_cmd import command as generate
from repowise.cli.commands.generate_cmd import engine
from repowise.cli.commands.init_cmd import _prepare
from repowise.cli.commands.init_cmd import command as init
from repowise.cli.platform import telemetry
from repowise.cli.ui import subscription
from repowise.core.generation.models import STUB_FALLBACK_ERROR
from repowise.core.providers.llm.specs import PROVIDER_KINDS

#: Every outcome key these commands add, with the values each may take
#: (``None`` = a boolean).
ALLOWED: dict[str, frozenset[str] | None] = {
    "interactive": None,
    "instruction_source": frozenset(init.INSTRUCTION_SOURCES | {"other"}),
    "subscription_cli_detected": None,
    "provider_kind": PROVIDER_KINDS,
    "outcome": frozenset({"generated", "partial", "failed", "dry_run", "refused"}),
    "refusal_reason": engine.REFUSAL_REASONS,
    "pages_written_bucket": frozenset(
        {"0", "1-9", "10-99", "100-499", "500-999", "1k-5k", "5k+"}
    ),
}


@pytest.fixture(autouse=True)
def _fresh_outcome():
    telemetry.drain_command_outcome()
    yield
    telemetry.drain_command_outcome()


def _drained() -> dict[str, Any]:
    outcome = telemetry.drain_command_outcome()
    for key, value in outcome.items():
        if key == "provider":
            continue  # a provider id from the fixed registry, as init reports it
        assert key in ALLOWED, key
        allowed = ALLOWED[key]
        if allowed is None:
            assert isinstance(value, bool), (key, value)
        else:
            assert value in allowed, (key, value)
    return outcome


# -- init ---------------------------------------------------------------------


@pytest.mark.parametrize("source", sorted(init.INSTRUCTION_SOURCES))
def test_a_known_instruction_source_is_reported(source: str) -> None:
    init._record_instruction_source(source)
    assert _drained() == {"instruction_source": source}


def test_any_other_source_text_never_leaves_the_machine() -> None:
    init._record_instruction_source("C:/Users/someone/notes.md?repo=secret")
    assert _drained() == {"instruction_source": "other"}


def test_no_source_reports_nothing() -> None:
    init._record_instruction_source(None)
    assert _drained() == {}


def test_init_reports_whether_it_ran_the_questions(tmp_path: Path, monkeypatch: Any) -> None:
    from repowise.cli.commands.init_cmd._options import InitOptions

    fields: dict[str, Any] = {name: None for name in InitOptions.__dataclass_fields__}
    fields.update(run_mode="standard", index_only=False, yes=True, resume=False, verbose=False)
    monkeypatch.setattr(_prepare, "configure_cli_logging", lambda **_k: None)
    run = _prepare._start_run(InitOptions(**fields), tmp_path, 0.0)
    assert run.is_interactive is False
    assert _drained() == {"interactive": False}


def test_a_signed_in_subscription_cli_is_noted_where_it_is_probed(monkeypatch: Any) -> None:
    plan = subscription.CliAuth("Claude team plan: uses your plan quota")
    monkeypatch.setattr(subscription, "agent_cli_auth", lambda n: plan if n == "claude_cli" else None)
    subscription.logged_in_agent_providers()
    assert _drained() == {"subscription_cli_detected": True}

    monkeypatch.setattr(subscription, "agent_cli_auth", lambda n: None)
    subscription.logged_in_agent_providers()
    assert _drained() == {"subscription_cli_detected": False}


# -- generate -----------------------------------------------------------------


def _page(pid: str, **meta: object) -> SimpleNamespace:
    return SimpleNamespace(page_id=pid, metadata=dict(meta))


def _outcome(pages: list[SimpleNamespace], planned: int = 3) -> SimpleNamespace:
    return SimpleNamespace(
        plan=SimpleNamespace(generate_ids={f"module_page:m{i}" for i in range(planned)}),
        generated_pages=pages,
        swept_page_ids=[],
    )


@pytest.mark.parametrize(
    ("pages", "expected"),
    [
        ([_page(f"module_page:m{i}") for i in range(3)], "generated"),
        ([_page("module_page:m0"), _page("module_page:m1", **{STUB_FALLBACK_ERROR: "x"})], "partial"),
        ([_page("module_page:m0", **{STUB_FALLBACK_ERROR: "x"})], "failed"),
    ],
)
def test_generate_reports_how_the_run_ended(pages: list, expected: str) -> None:
    generate._record_written(_outcome(pages))
    outcome = _drained()
    assert outcome["outcome"] == expected
    assert outcome["pages_written_bucket"] in {"0", "1-9"}


def test_a_decisions_only_run_wrote_no_pages() -> None:
    generate._record_written(_outcome([], planned=0))
    assert _drained() == {"outcome": "generated", "pages_written_bucket": "0"}


def test_generate_reports_the_provider_kind_not_the_model() -> None:
    generate._record_provider(SimpleNamespace(provider_name="claude_cli", model_name="secret-model"))
    outcome = _drained()
    assert outcome == {"provider": "claude_cli", "provider_kind": "subscription_cli"}


def test_a_refusal_names_a_fixed_reason_only() -> None:
    engine.record_refusal("declined")
    assert _drained() == {"outcome": "refused", "refusal_reason": "declined"}
    engine.record_refusal("Aborted by user at C:/secret")
    assert _drained() == {}


def test_generate_without_an_index_fails_with_a_reason(tmp_path: Path) -> None:
    with pytest.raises(click.ClickException) as raised:
        generate.generate_command.main([str(tmp_path)], standalone_mode=False)
    assert raised.value.reason == "no_index"


def test_the_event_dump_shows_the_command_fields(
    monkeypatch: Any, capsys: pytest.CaptureFixture
) -> None:
    """``REPOWISE_TELEMETRY_DEBUG`` prints exactly what would be sent, and sends nothing."""
    for var in ("DO_NOT_TRACK", "REPOWISE_TELEMETRY_DISABLED", "REPOWISE_OFFLINE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("REPOWISE_TELEMETRY_DEBUG", "1")
    monkeypatch.setattr("repowise.cli.platform.settings.is_enabled", lambda: True)

    generate._record_provider(SimpleNamespace(provider_name="anthropic"))
    engine.record_refusal("nothing_selected")
    telemetry.record_command_run("generate", extra=_drained())

    dumped = capsys.readouterr().err
    props = json.loads(dumped.split("would send:\n", 1)[1])["properties"]
    assert props["provider_kind"] == "api_key"
    assert props["outcome"] == "refused"
    assert props["refusal_reason"] == "nothing_selected"
