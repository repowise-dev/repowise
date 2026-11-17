"""The decisions layer's agent prompts, rendered once for the CLI and the web.

``repowise decision discover`` prints these and the dashboard copies them from
the server, so what an agent is told to keep is pinned here, once.
"""

from __future__ import annotations

import re

import pytest

from repowise.core.agent_prompts.decisions import (
    DISCOVERY_LIMIT,
    KEEP_TEST,
    RECORD_PROMPT_KINDS,
    decision_prompt,
    decision_record_prompt,
    render_add,
    render_agreements,
    render_discovery,
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
