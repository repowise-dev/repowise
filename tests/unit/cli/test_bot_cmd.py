"""``repowise bot status`` and ``repowise bot ai on|off``, and the init PR bot line.

What these protect:

* ``bot status --format json`` is the bot's part of the repo's setup status,
  its blocks as the platform sent them, plus the one next step; the exit code
  says who acts next: 0 nobody waits on a person, 3 a person does, 1 an error.
* A done bot step that still names an action (AI off, AI paused) is the next
  step; a settled one is not.
* An older repowise.dev without the AI and last-review fields still answers,
  with a note, never a traceback.
* ``bot ai`` patches the repo's review mode through the config route and
  carries the state that follows; a refusal a person clears exits 3.
* The init panel names the PR bot once per machine, under the hints switch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

CLAIM_URL = "https://repowise.dev/bot/installed?installation_id=123&src=agent_setup"
CHECK_URL = "https://github.com/acme/api/runs/987654"

BOT = {
    "installed": True,
    "enabled": True,
    "unclaimed": False,
    "install_url": "https://github.com/apps/repowise-bot/installations/new",
    "code": "on",
    "installation_id": 123,
    "claim_url": None,
    "ai": {"on": True, "code": "ai_on", "mode": "ai", "depth": "auto"},
    "credit": {
        "balance_cents": 412,
        "source": "personal",
        "cap_cents": 1000,
        "cap_is_default": True,
        "spent_this_month_cents": 120,
    },
    "last_review": {
        "at": "2026-11-18T09:12:00Z",
        "pr_number": 17,
        "status": "clean",
        "tier": "ai",
        "reason": None,
        "check_url": CHECK_URL,
    },
    "coverage_received": True,
}

BOT_STEP = {
    "id": "bot",
    "code": "on",
    "done": True,
    "who": "agent",
    "action": "PR reviews are on.",
    "url": "https://repowise.dev/settings/bot",
    "optional": False,
    "repo_id": "r-1",
}
COVERAGE_STEP = {
    "id": "coverage",
    "code": "coverage_received",
    "done": True,
    "who": "agent",
    "action": "Coverage reports arrive.",
    "url": None,
    "optional": True,
    "repo_id": "r-1",
}


def _setup(bot: dict | None = None, *steps: dict) -> dict:
    return {
        "account": {"signed_in": True, "user": "octo"},
        "tier": "pro",
        "effective_tier": "pro",
        "plan": {"trial": {"active": False}},
        "repo": {"full_name": "acme/api", "status": "ready"},
        "bot": BOT if bot is None else bot,
        "coverage": {"state": "reporting"},
        "steps": [
            {"id": "index", "code": "indexed", "done": True, "who": "agent"},
            *(steps or (BOT_STEP, COVERAGE_STEP)),
        ],
        "complete": True,
    }


NEEDS_CLAIM = _setup(
    {**BOT, "unclaimed": True, "code": "needs_claim", "claim_url": CLAIM_URL, "ai": None},
    {
        **BOT_STEP,
        "code": "needs_claim",
        "done": False,
        "who": "human",
        "action": "Open this link and confirm the Repowise PR bot install.",
        "url": CLAIM_URL,
    },
    COVERAGE_STEP,
)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch):
    from repowise.cli.platform import auth
    from repowise.cli.platform.client import PlatformClient

    state: dict = {
        "signed_in": True,
        "answers": [],  # setup-status answers, one per read; the last one repeats
        "answer": (200, _setup()),
        "action": (200, {"ok": True}),
        "gets": [],
        "sent": [],
    }
    monkeypatch.setattr(
        auth, "auth_headers", lambda: {"Authorization": "Bearer x"} if state["signed_in"] else {}
    )

    def get_json(self, path, *, params=None, timeout=None):
        state["gets"].append((path, params))
        return state["answers"].pop(0) if state["answers"] else state["answer"]

    def send_json(self, method, path, payload=None, *, timeout=None):
        state["sent"].append((method, path, payload))
        return state["action"]

    monkeypatch.setattr(PlatformClient, "get_json", get_json)
    monkeypatch.setattr(PlatformClient, "send_json", send_json)
    return state


def _bot(*args: str):
    from repowise.cli.commands.bot_cmd import bot_group

    return CliRunner().invoke(bot_group, list(args))


def _json(*args: str) -> tuple[int, dict]:
    result = _bot(*args, "--format", "json")
    return result.exit_code, json.loads(result.output)


# ---------------------------------------------------------------------------
# bot status
# ---------------------------------------------------------------------------


def test_status_carries_the_platform_blocks_and_exits_0_when_set_up(server) -> None:
    code, payload = _json("status", "--repo", "acme/api")
    assert code == 0
    assert server["gets"] == [("me/setup-status", {"via": "cli", "repo": "acme/api"})]
    assert payload["bot"] == BOT and payload["effective_tier"] == "pro"
    assert [s["id"] for s in payload["steps"]] == ["bot", "coverage"]
    assert payload["next"] is None and payload["complete"] is True
    assert "note" not in payload


def test_status_exits_3_with_the_claim_link_when_a_person_acts_next(server) -> None:
    server["answer"] = (200, NEEDS_CLAIM)
    code, payload = _json("status", "--repo", "acme/api")
    assert code == 3
    assert payload["next"] == {
        "code": "needs_claim",
        "who": "human",
        "action": "Open this link and confirm the Repowise PR bot install.",
        "url": CLAIM_URL,
        "optional": False,
    }
    assert payload["complete"] is False


def test_a_done_bot_step_with_ai_off_is_the_agents_next_step(server) -> None:
    ai_off = {**BOT_STEP, "code": "ai_off", "action": "Run `repowise bot ai on --repo acme/api`."}
    server["answer"] = (200, _setup(None, ai_off, COVERAGE_STEP))
    code, payload = _json("status", "--repo", "acme/api")
    assert code == 0
    assert payload["next"]["code"] == "ai_off" and payload["complete"] is True


def test_ai_paused_for_credit_is_a_person_step_but_reviews_still_count(server) -> None:
    paused = {**BOT_STEP, "code": "ai_paused_credit", "who": "human", "url": "https://x/topup"}
    server["answer"] = (200, _setup(None, paused, COVERAGE_STEP))
    code, payload = _json("status", "--repo", "acme/api")
    assert code == 3
    assert payload["next"]["url"] == "https://x/topup" and payload["complete"] is True


def test_a_required_step_comes_before_an_optional_one(server) -> None:
    coverage = {**COVERAGE_STEP, "code": "setup_pr_open", "done": False, "who": "human"}
    enable = {**BOT_STEP, "code": "ready_to_enable", "done": False}
    server["answer"] = (200, _setup(None, coverage, enable))
    code, payload = _json("status", "--repo", "acme/api")
    assert payload["next"]["code"] == "ready_to_enable" and code == 0


def test_an_optional_person_step_is_named_but_does_not_hold_an_agent(server) -> None:
    merge = {**COVERAGE_STEP, "code": "setup_pr_open", "done": False, "who": "human"}
    server["answer"] = (200, _setup(None, BOT_STEP, merge))
    code, payload = _json("status", "--repo", "acme/api")
    assert code == 0 and payload["complete"] is True
    assert payload["next"]["code"] == "setup_pr_open" and payload["next"]["optional"] is True


def test_no_bot_step_reported_carries_no_older_server_note(server) -> None:
    server["answer"] = (200, _setup({}, {"id": "index", "done": True}))
    code, payload = _json("status", "--repo", "acme/api")
    assert code == 0 and payload["next"] is None and payload["complete"] is False
    assert "note" not in payload


def test_status_table_shows_the_state_and_the_next_step(server) -> None:
    server["answer"] = (200, NEEDS_CLAIM)
    result = _bot("status", "--repo", "acme/api")
    assert result.exit_code == 3
    for text in (
        "acme/api",
        "installed, not claimed",
        "confirm the Repowise PR bot install",
        "installation_id=123",
        "PR #17: clean (ai)",
        "runs/987654",
        "$4.12 left, $1.20 of $10.00 monthly limit spent",
        "Coverage",
    ):
        assert text in result.output, text


def test_status_on_a_server_without_the_ai_fields_still_answers(server) -> None:
    old_bot = {"installed": True, "enabled": True, "unclaimed": False, "install_url": None}
    old_step = {k: v for k, v in BOT_STEP.items() if k != "code"}
    server["answer"] = (200, _setup(old_bot, old_step))
    code, payload = _json("status", "--repo", "acme/api")
    assert code == 0 and payload["next"] is None
    assert "does not report AI review" in payload["note"]
    assert "does not report AI review" in _bot("status", "--repo", "acme/api").output


def test_status_signed_out_is_a_person_step(server) -> None:
    server["signed_in"] = False
    code, payload = _json("status", "--repo", "acme/api")
    assert code == 3
    assert payload["error"] == "signed_out" and payload["repo"] == "acme/api"
    assert server["gets"] == []


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        ((404, {"detail": "Not Found"}), "unsupported"),
        ((0, {}), "offline"),
        ((500, {}), "http_500"),
    ],
)
def test_status_failures_exit_1(server, answer, error) -> None:
    server["answer"] = answer
    code, payload = _json("status", "--repo", "acme/api")
    assert code == 1 and payload["error"] == error


def test_status_needs_a_repo(server, monkeypatch: pytest.MonkeyPatch) -> None:
    from repowise.cli.platform import publish

    monkeypatch.setattr(publish, "read_remote", lambda _path: None)
    result = _bot("status")
    assert result.exit_code == 2 and "--repo" in result.output
    code, payload = _json("status")
    assert code == 1 and payload["error"] == "no_repo"


# ---------------------------------------------------------------------------
# bot ai on|off
# ---------------------------------------------------------------------------


def test_ai_on_patches_the_review_mode_and_reports_the_new_state(server) -> None:
    code, payload = _json("ai", "on", "--depth", "deep", "--repo", "acme/api")
    assert code == 0
    assert server["sent"] == [
        (
            "PATCH",
            "github-app/bot-repos/r-1/config",
            {"config": {"review_mode": "ai", "review_depth": "deep"}},
        )
    ]
    assert payload["ok"] is True and payload["message"] == "AI review is on for acme/api."
    assert payload["status"]["bot"] == BOT


def test_ai_off_keeps_the_signal_checks(server) -> None:
    result = _bot("ai", "off", "--repo", "acme/api")
    assert result.exit_code == 0, result.output
    assert server["sent"][0][2] == {"config": {"review_mode": "signals"}}
    assert "AI review is off for acme/api." in result.output


def test_ai_off_refuses_a_depth(server) -> None:
    assert _bot("ai", "off", "--depth", "deep", "--repo", "acme/api").exit_code == 2
    assert server["sent"] == []
    code, payload = _json("ai", "off", "--depth", "deep", "--repo", "acme/api")
    assert code == 1 and payload["error"] == "bad_usage"


def test_ai_on_done_but_the_state_after_it_unreadable_exits_2(server) -> None:
    server["answers"] = [(200, _setup()), (0, {})]
    code, payload = _json("ai", "on", "--repo", "acme/api")
    assert code == 2 and payload["ok"] is True and payload["status"] is None


def test_ai_on_done_but_a_person_acts_next_exits_3(server) -> None:
    paused = {**BOT_STEP, "code": "ai_paused_credit", "who": "human"}
    server["answers"] = [(200, _setup()), (200, _setup(None, paused))]
    code, payload = _json("ai", "on", "--repo", "acme/api")
    assert code == 3 and payload["ok"] is True
    assert payload["status"]["next"]["code"] == "ai_paused_credit"


@pytest.mark.parametrize(
    ("answer", "error", "exit_code"),
    [
        ((402, {"detail": {"code": "pr_bot_ai_requires_paid"}}), "needs_plan", 3),
        (
            (422, {"detail": {"code": "bot_config_locked", "message": "Locked: review_mode"}}),
            "locked",
            3,
        ),
        ((403, {"detail": "API key missing required scope: write"}), "forbidden", 3),
        ((404, {"detail": "Repo not found"}), "not_installed", 3),
        ((500, {}), "http_500", 1),
    ],
)
def test_ai_on_refusals_say_who_clears_them(server, answer, error, exit_code) -> None:
    server["action"] = answer
    code, payload = _json("ai", "on", "--repo", "acme/api")
    assert code == exit_code
    assert payload["ok"] is False and payload["error"] == error
    assert payload["status"]["bot"] == BOT


def test_ai_on_needs_plan_names_ai_review_not_private_repos(server) -> None:
    server["action"] = (402, {"detail": {"code": "pr_bot_ai_requires_paid"}})
    message = _json("ai", "on", "--repo", "acme/api")[1]["message"]
    assert "AI review needs a paid plan" in message and "settings?tab=billing" in message


def test_ai_on_locked_names_the_admin_and_the_keys(server) -> None:
    server["action"] = (
        422,
        {"detail": {"code": "bot_config_locked", "message": "Locked: review_mode"}},
    )
    message = _json("ai", "on", "--repo", "acme/api")[1]["message"]
    assert "Locked: review_mode" in message and "admin of the organization" in message


def test_ai_on_locked_without_a_message_still_reads(server) -> None:
    server["action"] = (422, {"detail": {"code": "bot_config_locked"}})
    message = _json("ai", "on", "--repo", "acme/api")[1]["message"]
    assert "None" not in message and "bot policy" in message


def test_ai_on_offline_exits_1_without_reading_the_state_again(server) -> None:
    server["action"] = (0, {})
    code, payload = _json("ai", "on", "--repo", "acme/api")
    assert code == 1 and payload["status"] is None
    assert len(server["gets"]) == 1  # the read that found the repo's id


def test_ai_on_where_the_bot_is_not_installed_sends_nothing(server) -> None:
    server["answer"] = (200, _setup({"installed": False}, {**BOT_STEP, "repo_id": None}))
    code, payload = _json("ai", "on", "--repo", "acme/api")
    assert code == 3 and payload["error"] == "not_installed"
    assert server["sent"] == []


def test_ai_signed_out_is_a_person_step(server) -> None:
    server["signed_in"] = False
    code, payload = _json("ai", "on", "--repo", "acme/api")
    assert code == 3 and payload["error"] == "signed_out"
    assert server["sent"] == []


# ---------------------------------------------------------------------------
# init: the PR bot line
# ---------------------------------------------------------------------------


@pytest.fixture
def not_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    from repowise.cli.platform.telemetry import environment

    monkeypatch.setattr(environment, "is_ci", lambda: False)
    monkeypatch.delenv("REPOWISE_NO_HINTS", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)


def test_the_pr_bot_link_is_given_once(not_ci) -> None:
    from repowise.cli.hints import pr_bot_link_once

    assert pr_bot_link_once() == "https://repowise.dev/bot/install?src=cli-init"
    assert pr_bot_link_once() is None


def test_the_pr_bot_link_respects_the_hints_switch(not_ci, monkeypatch) -> None:
    from repowise.cli import hints

    monkeypatch.setenv("REPOWISE_NO_HINTS", "1")
    assert hints.pr_bot_link_once() is None
    monkeypatch.delenv("REPOWISE_NO_HINTS")
    hints.set_enabled(False)  # repowise config hints off
    assert hints.pr_bot_link_once() is None
    hints.set_enabled(True)
    assert hints.pr_bot_link_once() is not None  # not spent while it was off


def test_the_pr_bot_link_is_off_in_ci() -> None:
    from repowise.cli.hints import pr_bot_link_once

    assert pr_bot_link_once() is None  # pytest counts as CI


def test_the_finish_panel_footer_carries_the_link() -> None:
    from rich.console import Console

    from repowise.cli.ui.finish_panel import FinishFacts, _footer

    url = "https://repowise.dev/bot/install?src=cli-init"
    facts = FinishFacts(
        scope={},
        files=1,
        symbols=1,
        languages="python",
        elapsed="1s",
        pr_bot_url=url,
    )
    console = Console(record=True, width=200)
    for line in _footer(facts, Path("."), []):
        console.print(line)
    assert url in console.export_text()
    assert _footer(FinishFacts(**{**facts.__dict__, "pr_bot_url": None}), Path("."), []) == []
