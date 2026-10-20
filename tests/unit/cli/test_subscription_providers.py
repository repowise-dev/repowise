"""Own-subscription providers are detected and offered, never picked for the user."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console

from repowise.cli.commands.init_cmd import _provider
from repowise.cli.cost_gate import spend_phrase
from repowise.cli.ui import provider_selection as ui
from repowise.cli.ui import subscription
from repowise.cli.ui.brand import THEME
from repowise.core.cost_estimator import approximate_generation_plan, estimate_cost
from repowise.core.providers.llm import resolve
from repowise.core.providers.llm.specs import PROVIDER_SPECS

from ._provider_picker import pick_default

_KEYS = tuple(
    var for spec in PROVIDER_SPECS.values() for var in (*spec.api_key_envs, *spec.base_url_envs)
)


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch: Any, tmp_path: Path) -> None:
    for var in (*_KEYS, "REPOWISE_PROVIDER", "REPOWISE_CLAUDE_CLI_CONCURRENCY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("REPOWISE_CONFIG_DIR", str(tmp_path / "global"))
    monkeypatch.setattr(ui, "_detect_ollama_status", lambda: False)
    resolve.clear_cache()


_PLAN = subscription.CliAuth("Claude team plan: uses your plan quota")


def _logged_in(monkeypatch: Any, *names: str, auth: Any = _PLAN) -> None:
    monkeypatch.setattr(subscription, "agent_cli_auth", lambda name: auth if name in names else None)


def _default_pick(monkeypatch: Any, repo: Path | None = None, **kwargs: Any) -> tuple[str, str]:
    return pick_default(monkeypatch, repo_path=repo, **kwargs)


# -- detection ----------------------------------------------------------------


def test_ready_agent_providers_lists_only_logged_in_clis(monkeypatch: Any) -> None:
    _logged_in(monkeypatch, "claude_cli")
    assert subscription.ready_agent_providers() == ("claude_cli",)
    _logged_in(monkeypatch)
    assert subscription.ready_agent_providers() == ()


def test_picker_and_note_share_one_detection(monkeypatch: Any) -> None:
    _logged_in(monkeypatch, "codex_cli", "claude_cli")
    detected = ui._detect_provider_status()
    assert [p for p in detected if PROVIDER_SPECS[p].agent] == list(
        subscription.ready_agent_providers()
    )


# -- picker: same keys and order as auto-detection ------------------------------


def test_picker_default_matches_autodetect_order(monkeypatch: Any) -> None:
    _logged_in(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert resolve.resolve_llm(None).provider == "anthropic"
    assert _default_pick(monkeypatch)[0] == "anthropic"


def test_picker_reads_repo_env_and_stored_keys(monkeypatch: Any, tmp_path: Path) -> None:
    _logged_in(monkeypatch)
    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    (repo / ".repowise" / ".env").write_text("DEEPSEEK_API_KEY=d\n", encoding="utf-8")
    stored = tmp_path / "global" / "provider_config.json"
    stored.parent.mkdir(parents=True)
    stored.write_text(json.dumps({"keys": {"kimi": "k"}}), encoding="utf-8")

    detected = ui._detect_provider_status(repo)

    assert list(detected) == ["deepseek", "kimi"]
    assert _default_pick(monkeypatch, repo)[0] == "deepseek"


def test_a_key_outranks_a_logged_in_cli_without_a_preference(monkeypatch: Any) -> None:
    _logged_in(monkeypatch, "claude_cli")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert _default_pick(monkeypatch)[0] == "openai"


def test_picker_shows_quota_and_time_for_a_logged_in_cli(monkeypatch: Any) -> None:
    _logged_in(monkeypatch, "claude_cli")
    _chosen, out = _default_pick(
        monkeypatch, subscription=subscription.SubscriptionContext(model_calls=14, concurrency=10)
    )
    assert "claude_cli: Claude team plan: uses your plan quota, about 14 model calls, 4 at a time" in out
    assert "about 4 min" in out
    assert "$0.00" not in out


def test_picker_without_an_estimate_prints_no_quota_line(monkeypatch: Any) -> None:
    _logged_in(monkeypatch, "claude_cli")
    assert "model calls" not in _default_pick(monkeypatch)[1]


# -- the estimate ---------------------------------------------------------------


def test_estimate_divides_calls_by_the_process_limit(monkeypatch: Any) -> None:
    text = subscription.subscription_estimate("claude_cli", 40, 10)
    assert "about 40 model calls, 4 at a time, about 10 min" in text
    monkeypatch.setenv("REPOWISE_CLAUDE_CLI_CONCURRENCY", "8")
    assert "8 at a time, about 5 min" in subscription.subscription_estimate("claude_cli", 40, 10)
    # A lower --concurrency still lowers it.
    assert "2 at a time, about 20 min" in subscription.subscription_estimate("claude_cli", 40, 2)


def test_spend_phrase_prices_api_providers_and_estimates_subscriptions() -> None:
    plans = approximate_generation_plan(50)
    calls = subscription.model_call_count(plans)
    cli = spend_phrase(estimate_cost(plans, "claude_cli", "claude_cli/claude-haiku-5-5"), 10)
    assert cli.startswith("Uses your Claude Code login")
    assert f"about {calls} model calls" in cli
    assert "$" not in cli
    assert spend_phrase(estimate_cost(plans, "gemini", "gemini-3.5-flash-lite")).startswith(
        "Estimated"
    )


# -- the --yes note -------------------------------------------------------------


def _run(**overrides: Any) -> SimpleNamespace:
    opts = SimpleNamespace(provider_name=overrides.pop("provider_name", None), index_only=True)
    fields: dict[str, Any] = {"is_interactive": False, "config": {}, "no_provider": False}
    return SimpleNamespace(opts=opts, **{**fields, **overrides})


def _note(monkeypatch: Any, run: SimpleNamespace) -> str:
    buf = io.StringIO()
    monkeypatch.setattr(_provider, "console", Console(file=buf, width=200, theme=THEME))
    _provider.note_subscription_cli(run)
    return buf.getvalue()


def test_yes_run_names_the_logged_in_cli_and_stays_free(monkeypatch: Any) -> None:
    _logged_in(monkeypatch, "claude_cli")
    out = _note(monkeypatch, _run())
    assert "Claude Code is signed in (Claude team plan: uses your plan quota)" in out
    assert "repowise update --full --provider claude_cli" in out


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider_name": "gemini"},
        {"is_interactive": True},
        {"config": {"provider": "openai"}},
    ],
)
def test_note_is_quiet_when_the_user_already_chose(monkeypatch: Any, overrides: dict) -> None:
    _logged_in(monkeypatch, "claude_cli")
    assert _note(monkeypatch, _run(**overrides)) == ""


def test_note_is_quiet_when_a_model_writes_the_docs(monkeypatch: Any) -> None:
    _logged_in(monkeypatch, "claude_cli")
    run = _run()
    run.opts.index_only = False
    assert _note(monkeypatch, run) == ""


def test_note_is_quiet_without_a_logged_in_cli(monkeypatch: Any) -> None:
    _logged_in(monkeypatch)
    assert _note(monkeypatch, _run()) == ""


def test_claude_code_is_named_before_codex(monkeypatch: Any) -> None:
    _logged_in(monkeypatch, "codex_cli", "claude_cli")
    assert subscription.ready_agent_providers()[0] == "claude_cli"
    assert "Claude Code is signed in" in _note(monkeypatch, _run())


def test_a_cli_without_a_plan_is_listed_but_never_offered(monkeypatch: Any) -> None:
    _logged_in(monkeypatch, "opencode", auth=subscription.CliAuth("no plan", offer=False))
    assert subscription.ready_agent_providers() == ()
    assert _note(monkeypatch, _run()) == ""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    chosen, out = _default_pick(
        monkeypatch, subscription=subscription.SubscriptionContext(prefer=("opencode",))
    )
    assert chosen == "openai"
    assert "opencode: no plan, not recommended" in out


# -- what each CLI's status command says ------------------------------------------


def _claude(**fields: Any) -> str:
    return json.dumps({"loggedIn": True, "apiProvider": "firstParty", **fields})


@pytest.mark.parametrize(
    ("name", "output", "billing", "offer"),
    [
        ("claude_cli", _claude(authMethod="claude.ai", subscriptionType="max"),
         "Claude max plan: uses your plan quota", True),
        ("claude_cli", _claude(authMethod="claude.ai", subscriptionType=None),
         "signed in without a paid Claude plan", False),
        ("claude_cli", _claude(authMethod="api_key"), "API key: billed per token", True),
        ("claude_cli", _claude(authMethod="claude.ai", apiProvider="bedrock"),
         "runs through bedrock: billed to that cloud account", True),
        ("claude_cli", "not json", "Claude Code login; it runs on whatever that login is "
         "billed to", True),
        ("codex_cli", "Logged in using ChatGPT",
         "signed in with ChatGPT: uses that plan's Codex quota, if the plan is active", False),
        ("codex_cli", "Logged in using an API key - sk-proj-***", "API key: billed per token",
         True),
        ("codex_cli", "Logged in somehow", "Codex CLI login; it runs on whatever that login "
         "is billed to", True),
        ("opencode", "\x1b[0m\u250c  Credentials\n\u2514  0 credentials",
         "no stored credentials: runs on whatever provider its own config names", False),
        ("opencode", "\u2514  2 credentials",
         "runs on the provider its stored credentials name, billed there", True),
    ],
)
def test_status_output_maps_to_billing(
    monkeypatch: Any, name: str, output: str, billing: str, offer: bool
) -> None:
    import subprocess

    from repowise.core.agents.identity import AgentIdentity

    monkeypatch.setattr(AgentIdentity, "which", lambda self: self.executable)
    monkeypatch.setattr(
        subprocess, "run", lambda args, **_k: subprocess.CompletedProcess(args, 0, output, "")
    )
    assert subscription.agent_cli_auth(name) == subscription.CliAuth(billing, offer)


@pytest.mark.parametrize(
    ("name", "returncode", "output"),
    [
        ("claude_cli", 1, _claude(loggedIn=False)),
        ("claude_cli", 0, json.dumps({"loggedIn": False})),
        ("codex_cli", 1, "Not logged in"),
    ],
)
def test_signed_out_cli_is_not_detected(
    monkeypatch: Any, name: str, returncode: int, output: str
) -> None:
    import subprocess

    from repowise.core.agents.identity import AgentIdentity

    monkeypatch.setattr(AgentIdentity, "which", lambda self: self.executable)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **_k: subprocess.CompletedProcess(args, returncode, output, ""),
    )
    assert subscription.agent_cli_auth(name) is None
