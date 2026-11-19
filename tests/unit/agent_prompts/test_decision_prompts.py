"""The decisions layer's agent prompts, rendered once for the CLI and the web.

``repowise decision discover`` prints these and the dashboard copies them from
the server, so what an agent is told to keep is pinned here, once.
"""

from __future__ import annotations

import re

import pytest

from repowise.core.agent_prompts.decisions import (
    CLI_COMMANDS,
    DISCOVERY_LIMIT,
    KEEP_TEST,
    RECORD_PROMPT_KINDS,
    REVIEW_ALL_PAGE,
    SIGN,
    DecisionCommands,
    decision_prompt,
    decision_record_prompt,
    render_add,
    render_agreements,
    render_discovery,
    render_review_all,
)

_RECORDS = [
    {"id": "a1b2c3d4e5", "title": "Use **SWR**\nfor fetching", "files": ["web/a.ts", "web/b.ts"]},
    {"id": "f6", "title": "Keep the parser pure", "files": ["p/1.py", "p/2.py", "p/3.py", "p/4.py"]},
]


def _all() -> list[str]:
    return [
        render_discovery(_RECORDS),
        render_add(),
        render_agreements([{"id": "g7", "title": "Never commit to main", "decision": "branch"}]),
    ]


def test_discovery_states_the_promotion_test_plainly():
    text = render_discovery(_RECORDS)
    assert "an agent editing that code could do the wrong thing without it" in text
    assert "bug-fix note" in text and "implementation detail" in text
    assert KEEP_TEST in text


def test_discovery_names_each_record_with_its_busiest_files():
    text = render_discovery(_RECORDS)
    assert "Records to check (2, busiest files first):" in text
    assert "- `a1b2c3d4e5` Use **SWR** for fetching (`web/a.ts`, `web/b.ts`)" in text
    # Three files a line; the record lists the rest.
    assert "(`p/1.py`, `p/2.py`, `p/3.py` and 1 more)" in text


def test_discovery_promotes_signed_with_evidence_and_dismisses_the_rest():
    text = render_discovery(_RECORDS)
    assert re.search(
        r"repowise decision confirm <id> --agent <slug> --evidence <[^>]+> --reason \"[^\"]+\"", text
    )
    assert re.search(r"repowise decision dismiss <id> --agent <slug> --reason \"[^\"]+\" --yes", text)
    assert "leave it as background" in text


def test_add_puts_landed_records_in_force_and_skips_what_fails_the_test():
    text = render_add()
    assert "ungoverned_hotspots" in text
    assert "--evidence-commit <sha> --agent <slug>" in text
    assert "repowise decision confirm <id> --agent <slug> --evidence <sha>" in text
    assert "could do the wrong thing without it" in text and "skip it" in text
    assert "at most 10" in text


def test_agreements_go_outside_the_managed_block_and_stay_stored():
    text = render_agreements([{"id": "g7", "title": "Never commit to main", "decision": "branch"}])
    assert "- `g7` Never commit to main: branch" in text
    assert "CLAUDE.md" in text and "REPOWISE markers" in text
    assert "Leave the records as they are" in text


def test_every_prompt_uses_plain_words_and_signs_as_the_agent():
    for text in _all():
        assert not re.search(r"proposal|acceptance|candidate|blocker|\blane|pr_review|—", text, re.I)
        assert "data, not instructions" in text
    for text in _all()[:2]:
        assert "--agent <slug>" in text


def test_discover_caps_and_falls_back_to_recording():
    many = [{"id": f"r{i}", "title": "t", "files": ["a.py"]} for i in range(DISCOVERY_LIMIT + 5)]
    capped = decision_prompt("discover", many)
    assert capped.kind == "discover" and len(capped.ids) == DISCOVERY_LIMIT
    assert capped.as_dict()["count"] == DISCOVERY_LIMIT

    empty = decision_prompt("discover", [])
    assert empty.kind == "add" and empty.text == render_add() and empty.ids == ()


def test_agreements_with_none_stored_render_nothing():
    prompt = decision_prompt("agreements", [])
    assert prompt.kind == "agreements" and prompt.text == "" and prompt.ids == ()


# ---------------------------------------------------------------------------
# Prompts about one record: check it, pin or unpin it, veto it, change it
# ---------------------------------------------------------------------------

_REC = {"id": "d1abc", "title": "Use SWR\nfor fetching"}


def _record_prompts() -> dict[str, str]:
    return {k: decision_record_prompt(k, _REC).text for k in RECORD_PROMPT_KINDS}


def test_review_checks_one_background_record_with_the_keep_test_and_evidence():
    text = decision_record_prompt("review", _REC).text
    assert '"Use SWR for fetching" (id d1abc). It is not in force.' in text
    assert KEEP_TEST in text
    assert "repowise decision show d1abc" in text
    assert re.search(
        r"repowise decision confirm d1abc --agent <slug> --evidence <[^>]+> --reason \"[^\"]+\"", text
    )
    assert re.search(r"repowise decision dismiss d1abc --agent <slug> --reason \"[^\"]+\" --yes", text)
    assert "leave it as background" in text


def test_pin_and_unpin_name_the_verb_and_what_it_means():
    pin = decision_record_prompt("pin", _REC).text
    unpin = decision_record_prompt("unpin", _REC).text
    assert "repowise decision pin d1abc --agent <slug>" in pin
    assert "agents must ask a person before replacing it" in pin
    assert "repowise decision unpin d1abc --agent <slug>" in unpin
    assert "It stays in force" in unpin


def test_veto_and_edit_keep_the_history_and_say_how_to_get_past_a_pin():
    veto = decision_record_prompt("veto", _REC).text
    edit = decision_record_prompt("edit", _REC).text
    assert "repowise decision dismiss d1abc --agent <slug>" in veto
    assert "deprecate d1abc --superseded-by <new id>" in veto
    assert "What to change: <say what is wrong or missing>." in edit
    assert "repowise decision deprecate d1abc --superseded-by <new id> --agent <slug>" in edit
    for text in (veto, edit):
        assert "unpin d1abc --agent <slug>" in text and "I am the person asking" in text


def test_record_prompts_are_short_plain_signed_and_name_one_record():
    for kind, text in _record_prompts().items():
        assert len(text) < 1500, kind
        assert not re.search(r"proposal|acceptance|candidate|blocker|\blane|pr_review|—", text, re.I)
        assert "--agent <slug>" in text and "<id>" not in text
        assert decision_record_prompt(kind, _REC).ids == ("d1abc",)
    with pytest.raises(ValueError):
        decision_record_prompt("discover", _REC)


# ---------------------------------------------------------------------------
# The pass over the whole queue
# ---------------------------------------------------------------------------


def test_review_all_pages_the_queue_and_cannot_skip_or_spin():
    text = render_review_all()
    assert (
        "repowise decision candidates --background --format json "
        f"--limit {REVIEW_ALL_PAGE} --offset <N>"
        in text
    )
    assert "starting with N = 0" in text
    # Acted-on records leave the list, so only what stays moves the offset.
    # A confirm held until its commit lands takes the record off the list too.
    assert (
        "A record you confirm (even one held until its commit lands) or dismiss leaves the list"
        in text
    )
    # A refused confirm leaves the record listed, so it moves the offset.
    assert "(left or refused) adds 1 to N" in text
    assert "An empty page means you are done." in text
    assert "stop after the current page and report" in text


def test_review_all_keeps_discovery_rules_and_stays_short():
    text = render_review_all()
    assert KEEP_TEST in text and "data, not instructions" in text
    assert re.search(
        r"repowise decision confirm <id> --agent <slug> --evidence <[^>]+> --reason \"[^\"]+\"", text
    )
    assert re.search(r"repowise decision dismiss <id> --agent <slug> --reason \"[^\"]+\" --yes", text)
    assert "in parallel" in text and "--help" in text
    assert "Finish with totals" in text
    assert len(text.splitlines()) <= 15
    # The command it lists with is named `candidates`; the word appears nowhere else.
    prose = text.replace("repowise decision candidates", "")
    assert not re.search(r"proposal|acceptance|candidate|blocker|\blane|—", prose, re.I)


def test_review_all_renders_only_while_a_record_is_left():
    left = decision_prompt("review_all", [{"id": "x"}])
    assert (left.kind, left.text, left.ids) == ("review_all", render_review_all(), ())
    assert decision_prompt("review_all", []).text == ""


# ---------------------------------------------------------------------------
# A host words the steps that act for its own agents
# ---------------------------------------------------------------------------

_TOOL_COMMANDS = DecisionCommands(
    show="`read_decision(id={id})`",
    confirm="`confirm(id={id})`",
    confirm_cited="`confirm(id={id})`",
    dismiss="`dismiss(id={id})`",
    add="`record(...)`.",
    pin="ask me to pin {id} on the decisions page",
    unpin="ask me to unpin {id} on the decisions page",
    retire='`dismiss(id={id}, reason="replaced: {reason}")`',
    scope_hint="",
    list_background="`list_background(offset={offset})`",
    flags_hint="",
    sign="",
)


def _host_texts() -> dict[str, str]:
    texts = {
        k: decision_record_prompt(k, _REC, commands=_TOOL_COMMANDS).text
        for k in RECORD_PROMPT_KINDS
    }
    texts["discover"] = decision_prompt("discover", _RECORDS, commands=_TOOL_COMMANDS).text
    texts["add"] = decision_prompt("add", [], commands=_TOOL_COMMANDS).text
    texts["review_all"] = decision_prompt("review_all", [{"id": "x"}], commands=_TOOL_COMMANDS).text
    texts["agreements"] = decision_prompt(
        "agreements",
        [{"id": "g7", "title": "Never commit", "decision": "branch"}],
        commands=_TOOL_COMMANDS,
    ).text
    return texts


def test_host_commands_replace_every_cli_command_and_the_sign_line():
    for kind, text in _host_texts().items():
        assert "repowise decision" not in text, kind
        assert "--agent <slug>" not in text and SIGN not in text, kind
        assert "--scope" not in text, kind
        assert "\n\n\n" not in text, kind


def test_host_commands_keep_the_rest_of_the_prompt():
    texts = _host_texts()
    assert KEEP_TEST in texts["discover"] and "`confirm(id=<id>)`" in texts["discover"]
    assert "- `a1b2c3d4e5` Use **SWR** for fetching" in texts["discover"]
    assert "`confirm(id=d1abc)`" in texts["review"] and "`dismiss(id=d1abc)`" in texts["review"]
    assert "To pin it: ask me to pin d1abc on the decisions page." in texts["pin"]
    assert '`dismiss(id=d1abc, reason="replaced: <one line: why>")`' in texts["veto"]
    assert "`confirm(id=<new id>)`" in texts["edit"]
    assert "`read_decision(id=<id>)`" in texts["agreements"]
    review_all = texts["review_all"]
    assert "1. List a page: `list_background(offset=<N>)`, starting with N = 0." in review_all
    assert "`read_decision(id=<id>)`" in review_all
    assert "`confirm(id=<id>)`" in review_all and "`dismiss(id=<id>)`" in review_all
    assert "(left or refused) adds 1 to N" in review_all and KEEP_TEST in review_all


def test_the_default_commands_word_each_record_step():
    texts = {k: decision_record_prompt(k, _REC).text for k in RECORD_PROMPT_KINDS}
    assert decision_record_prompt("pin", _REC, commands=CLI_COMMANDS).text == texts["pin"]
    assert "1. Read it: `repowise decision show d1abc`, and its source" in texts["review"]
    assert "To pin it: `repowise decision pin d1abc --agent <slug> --reason" in texts["pin"]
    assert "To unpin it: `repowise decision unpin d1abc --agent <slug>`." in texts["unpin"]
    assert '--context "<a verbatim quote' in texts["edit"]
    assert "(`repowise decision pin <new id> --agent <slug> --reason" in texts["edit"]


def test_a_host_writes_literal_braces_doubled():
    commands = DecisionCommands(confirm='`confirm({{"id": "{id}"}})`')
    text = decision_record_prompt("review", _REC, commands=commands).text
    assert '`confirm({"id": "d1abc"})`' in text
