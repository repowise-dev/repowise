"""Questions built from measured values, not from adjectives.

``page_suggestions`` turns one already-fetched tool result into questions the
page has earned; a value absent from the result produces no question, so the
caller keeps its own static tier. ``follow_up_suggestions`` turns the tools a
finished turn called into the next read worth making.

Both take tool results, never a repository path, so a host with its own artifact
store can feed them. Kept apart from ``routers/chat.py``'s
``_build_tool_summary``: that labels a row that already happened, this asks a
question nobody has asked yet.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

MAX_PAGE_SUGGESTIONS = 3
MAX_FOLLOW_UPS = 2

# A whole path crowds a 390px dock, so a target is named by its last segment.
_MAX_LABEL_CHARS = 48

_TARGET_SEPARATOR = ", "


def _label(target: str) -> str:
    cleaned = target.strip()
    tail = cleaned.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or cleaned
    if len(tail) > _MAX_LABEL_CHARS:
        return f"{tail[: _MAX_LABEL_CHARS - 1]}…"
    return tail


def _humanize(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").strip()


def _suggestion(text: str, source: str, tool_hint: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"text": text, "source": source}
    if tool_hint:
        entry["toolHint"] = tool_hint
    return entry


def _targets(result: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    targets = result.get("targets")
    if not isinstance(targets, dict):
        return {}
    return {k: v for k, v in targets.items() if isinstance(v, Mapping)}


def _worst_target(result: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]] | None:
    """Ranked by counted fixes then churn, so a multi-select page names the
    file carrying the most evidence rather than whichever came first."""
    entries = _targets(result)
    if not entries:
        return None

    def rank(item: tuple[str, Mapping[str, Any]]) -> tuple[float, float]:
        profile = item[1].get("defect_profile")
        fixes = profile.get("fix_count", 0) if isinstance(profile, Mapping) else 0
        hotspot = item[1].get("hotspot_score", 0.0)
        return (
            float(fixes) if isinstance(fixes, (int, float)) else 0.0,
            float(hotspot) if isinstance(hotspot, (int, float)) else 0.0,
        )

    return max(entries.items(), key=rank)


# --------------------------------------------------------------------------
# Page tier: one prefetched tool result -> questions naming what it measured
# --------------------------------------------------------------------------


def _from_get_context(target: str, result: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries = _targets(result)
    path, card = next(iter(entries.items()), (target, {}))
    name = _label(path)
    out: list[dict[str, Any]] = []

    history = card.get("fix_history")
    if isinstance(history, Mapping):
        count = history.get("fix_count")
        if isinstance(count, int) and count > 0:
            out.append(
                _suggestion(f"Explain the {count} bug fixes in {name}", "page", "get_risk")
            )
    if card.get("hotspot") is True:
        out.append(
            _suggestion(f"Why does {name} change so often?", "page", "get_risk")
        )
    episodes = card.get("episodes")
    if isinstance(episodes, int) and episodes > 0:
        out.append(
            _suggestion(f"What happened in {name} and why?", "page", "get_why")
        )
    return out


def _from_get_risk(target: str, result: Mapping[str, Any]) -> list[dict[str, Any]]:
    worst = _worst_target(result)
    if worst is None:
        return []
    path, card = worst
    name = _label(path)
    out: list[dict[str, Any]] = []

    profile = card.get("defect_profile")
    if isinstance(profile, Mapping):
        count = profile.get("fix_count")
        if isinstance(count, int) and count > 0:
            out.append(
                _suggestion(f"Explain the {count} bug fixes in {name}", "page", "get_risk")
            )

    biomarkers = card.get("top_biomarkers")
    if isinstance(biomarkers, Sequence) and biomarkers and isinstance(biomarkers[0], Mapping):
        top = biomarkers[0]
        kind = top.get("biomarker_type")
        function = top.get("function_name")
        if isinstance(kind, str) and kind:
            where = f" in {function}" if isinstance(function, str) and function else f" in {name}"
            out.append(
                _suggestion(
                    f"Explain the {_humanize(kind)} finding{where}", "page", "get_health"
                )
            )

    owner = card.get("primary_owner")
    owner_pct = card.get("owner_pct")
    if isinstance(owner, str) and owner and isinstance(owner_pct, (int, float)):
        out.append(
            _suggestion(
                f"{owner} wrote {round(owner_pct * 100)}% of {name}. Who else should review a change?",
                "page",
                "get_risk",
            )
        )

    partners = card.get("co_change_partners_total")
    if isinstance(partners, int) and partners > 0:
        out.append(
            _suggestion(
                f"What are the {partners} files that change with {name}?", "page", "get_risk"
            )
        )
    return out


def _from_get_health(target: str, result: Mapping[str, Any]) -> list[dict[str, Any]]:
    findings = result.get("findings")
    if not isinstance(findings, Sequence):
        findings = result.get("top_findings")
    out: list[dict[str, Any]] = []

    if isinstance(findings, Sequence) and findings and isinstance(findings[0], Mapping):
        top = findings[0]
        kind = top.get("biomarker_type")
        function = top.get("function_name")
        file_path = top.get("file_path")
        where = (
            f" in {function}"
            if isinstance(function, str) and function
            else f" in {_label(file_path)}"
            if isinstance(file_path, str) and file_path
            else ""
        )
        if isinstance(kind, str) and kind:
            out.append(
                _suggestion(
                    f"Explain the {_humanize(kind)} finding{where}", "page", "get_health"
                )
            )

    total = result.get("findings_total")
    if not isinstance(total, int):
        total = result.get("top_findings_total")
    if isinstance(total, int) and total > 1:
        out.append(
            _suggestion(
                f"Which of the {total} findings should I fix first?", "page", "get_health"
            )
        )

    metrics = result.get("metrics")
    if isinstance(metrics, Sequence) and metrics and isinstance(metrics[0], Mapping):
        metric = metrics[0]
        score = metric.get("score")
        path = metric.get("file_path")
        if isinstance(score, (int, float)) and isinstance(path, str) and path:
            out.append(
                _suggestion(
                    f"Why does {_label(path)} score {round(float(score), 1)} out of 10?",
                    "page",
                    "get_health",
                )
            )
    return out


def _from_get_why(target: str, result: Mapping[str, Any]) -> list[dict[str, Any]]:
    decisions = result.get("decisions")
    out: list[dict[str, Any]] = []
    if isinstance(decisions, Sequence) and decisions and isinstance(decisions[0], Mapping):
        title = decisions[0].get("title")
        if isinstance(title, str) and title.strip():
            short = title.strip()
            if len(short) > _MAX_LABEL_CHARS:
                short = f"{short[: _MAX_LABEL_CHARS - 1]}…"
            out.append(_suggestion(f'Why was "{short}" decided?', "page", "get_why"))
            out.append(
                _suggestion(
                    f'Has later work conflicted with "{short}"?', "page", "get_why"
                )
            )

    alignment = result.get("alignment")
    if isinstance(alignment, Mapping):
        score = alignment.get("score")
        if score is not None:
            out.append(
                _suggestion(
                    f"Alignment scores {score}. Where does the code diverge?",
                    "page",
                    "get_why",
                )
            )
    return out


def _from_get_change_risk(target: str, result: Mapping[str, Any]) -> list[dict[str, Any]]:
    ref = _label(str(result.get("ref") or target or "this change"))
    out: list[dict[str, Any]] = []

    # ``drivers`` is diagnostics-gated and absent from the prefetch, so the
    # measured values named here are the ones the default response always
    # carries.
    classification = result.get("classification") or result.get("review_priority")
    if isinstance(classification, str) and classification:
        percentile = result.get("risk_percentile")
        # Stored to one decimal; every other surface shows it whole.
        measured = (
            f" at p{round(percentile)}" if isinstance(percentile, (int, float)) else ""
        )
        out.append(
            _suggestion(
                f"Why is {ref} rated {classification}{measured}?", "page", "get_change_risk"
            )
        )
    if result.get("is_fix") is True:
        out.append(_suggestion(f"What did {ref} fix?", "page", "get_change_risk"))
    if isinstance(classification, str) and classification:
        out.append(
            _suggestion(
                f"Which files in {ref} deserve the closest review?", "page", "get_risk"
            )
        )
    return out


_PAGE_DERIVATIONS = {
    "get_context": _from_get_context,
    "get_risk": _from_get_risk,
    "get_health": _from_get_health,
    "get_why": _from_get_why,
    "get_change_risk": _from_get_change_risk,
}


def page_suggestions(
    tool_name: str,
    target: str,
    result: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Questions this result earned, most specific first; empty when nothing
    was measured. ``get_symbol`` returns source and no figures, so it derives
    nothing and the caller's static tier stands."""
    if not isinstance(result, Mapping) or "error" in result:
        return []
    derive = _PAGE_DERIVATIONS.get(tool_name)
    if derive is None:
        return []
    primary = target.split(_TARGET_SEPARATOR)[0].strip() if target else ""
    return _dedupe(derive(primary, result))[:MAX_PAGE_SUGGESTIONS]


# --------------------------------------------------------------------------
# Follow-up tier: what a finished turn read -> the next read worth making
# --------------------------------------------------------------------------


# (templated, plain, next tool). The plain form stands in when the call named no
# subject, so no chip ever reads "changing ?".
_FOLLOW_UPS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "get_context": (
        ("What is risky about changing {subject}?", "What is risky about changing this?", "get_risk"),
        ("Which decisions govern {subject}?", "Which decisions govern this code?", "get_why"),
    ),
    "get_risk": (
        ("Which tests reduce the most uncertainty for {subject}?", "Which tests reduce the most uncertainty?", "get_risk"),
        ("Which decisions govern {subject}?", "Which decisions govern this code?", "get_why"),
    ),
    "get_health": (
        ("Propose a safe refactoring sequence for {subject}", "Propose a safe refactoring sequence for these findings", "get_health"),
        ("What breaks if I change {subject}?", "What breaks if I change these files?", "get_risk"),
    ),
    "get_why": (
        ("Show the code {subject} affects", "Show the code these decisions affect", "get_context"),
        ("Has later work conflicted with this?", "Has later work conflicted with this?", "get_why"),
    ),
    "get_symbol": (
        ("Who calls {subject}?", "Who calls this symbol?", "get_context"),
        ("What behavior should tests protect in {subject}?", "What behavior should tests protect here?", "get_risk"),
    ),
    "get_change_risk": (
        ("Which files in {subject} deserve the closest review?", "Which files in this change deserve the closest review?", "get_risk"),
        ("What tests should validate {subject}?", "What tests should validate this change?", "get_risk"),
    ),
    "search_codebase": (
        ("Explain the most relevant result in depth", "Explain the most relevant result in depth", "get_context"),
        ("Which of these results are riskiest to modify?", "Which of these results are riskiest to modify?", "get_risk"),
    ),
    "get_overview": (
        ("Which files are riskiest to modify?", "Which files are riskiest to modify?", "get_risk"),
        ("What architectural decisions have been made?", "What architectural decisions have been made?", "get_why"),
    ),
    "get_dead_code": (
        ("Is the highest-confidence result safe to remove?", "Is the highest-confidence result safe to remove?", "get_risk"),
        ("What should I check before deleting it?", "What should I check before deleting it?", "get_context"),
    ),
}


def _follow_ups_for(tool_name: str, arguments: Mapping[str, Any]) -> list[dict[str, Any]]:
    subject = _subject(arguments)
    return [
        _suggestion(
            templated.format(subject=subject) if subject else plain,
            "followup",
            hint,
        )
        for templated, plain, hint in _FOLLOW_UPS.get(tool_name, ())
    ]


def _subject(arguments: Mapping[str, Any]) -> str:
    for key in ("targets", "symbol_id", "revspec", "id", "query"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return _label(value)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            first = next((v for v in value if isinstance(v, str) and v.strip()), None)
            if first:
                return _label(first)
    return ""


def _errored(call: Mapping[str, Any]) -> bool:
    """A call whose result carries an error taught the turn nothing, so it
    cannot justify a next step or name the conversation. ``run_grounding``
    already drops the prefetch on the same rule."""
    artifact = call.get("artifact")
    data = artifact.get("data") if isinstance(artifact, Mapping) else None
    return isinstance(data, Mapping) and "error" in data


def tool_names(tool_calls: Sequence[Mapping[str, Any]]) -> list[str]:
    """The tools a turn actually learned something from, in call order."""
    return [
        call["name"]
        for call in tool_calls
        if isinstance(call.get("name"), str) and not _errored(call)
    ]


def _dedupe(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for entry in entries:
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip() or text in seen:
            continue
        seen.add(text)
        out.append(dict(entry))
    return out


def follow_up_suggestions(
    tool_calls: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Newest call first, so follow-ups sit on the freshest evidence. A turn
    that called no tool earns none."""
    out: list[dict[str, Any]] = []
    for call in reversed(list(tool_calls)):
        name = call.get("name")
        if not isinstance(name, str) or _errored(call):
            continue
        arguments = call.get("arguments")
        out.extend(
            _follow_ups_for(name, arguments if isinstance(arguments, Mapping) else {})
        )
        if len(_dedupe(out)) >= MAX_FOLLOW_UPS:
            break
    return _dedupe(out)[:MAX_FOLLOW_UPS]


# --------------------------------------------------------------------------
# Conversation titles
# --------------------------------------------------------------------------

_TITLE_MAX_CHARS = 60
_TITLE_STOPWORDS = {
    "a", "an", "and", "are", "can", "did", "do", "does", "for", "give",
    "how", "in", "is", "me", "of", "on", "please", "show", "tell", "the",
    "this", "to", "what", "whats", "why", "you",
}
# A tool names the subject of the conversation better than the question's own
# opening words do, which are usually "what does" or "explain the".
_TOOL_SUBJECTS = {
    "get_risk": "Risk",
    "get_change_risk": "Change risk",
    "get_health": "Code health",
    "get_why": "Decisions",
    "get_dead_code": "Dead code",
    "get_context": "Context",
    "get_symbol": "Symbol",
    "get_overview": "Overview",
    "search_codebase": "Search",
}


def conversation_title(question: str, tool_names: Sequence[str]) -> str:
    """The first question, prefixed by what the turn read.

    No model call: a second provider round trip per new conversation would cost
    every first answer latency for a string that only has to be recognisable.
    """
    words = question.split()
    keep = [w for w in words if w.strip(".,?!:;\"'").lower() not in _TITLE_STOPWORDS]
    body = " ".join(keep or words).strip(".,?!:;\"' ")
    if not body:
        body = " ".join(words[:6]).strip() or "New conversation"

    subject = next(
        (_TOOL_SUBJECTS[name] for name in tool_names if name in _TOOL_SUBJECTS), None
    )
    title = f"{subject}: {body}" if subject else body
    if len(title) > _TITLE_MAX_CHARS:
        title = f"{title[: _TITLE_MAX_CHARS - 1].rstrip()}…"
    return title
