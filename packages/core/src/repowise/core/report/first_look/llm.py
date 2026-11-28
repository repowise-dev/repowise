"""One model call that rewrites the repo report's prose.

The model gets facts only (rule, typed facts, counts, basenames where the
prose needs a name), never source text, a secret's value or a person's name.
It returns plain sentences keyed by the ids it was sent. Every guard after the
call is deterministic, and the call is never repeated: the prompt holds the
shape, and whatever fails a guard keeps the template's text.

Links, chips, paths and prompts are rendered from item ids, never from this
text, so the model can word an item but cannot point it anywhere.
"""

from __future__ import annotations

import contextlib
import copy
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from repowise.core.analysis.health.fix_first.text import basename

from .model import SECTION_CAP, FirstLookReport, Item, SectionBlock

log = structlog.get_logger(__name__)

#: The one call's answer budget; a reply cut here keeps the template for the rest.
MAX_TOKENS = 6_000
#: The label the call's spend is recorded under (``repowise status``, costs).
OPERATION = "first_look"

SYSTEM_PROMPT = """\
You write the repo report a developer reads after Repowise indexes their \
repository. Plain language, direct, no hedging, no praise filler.

Input: one JSON object. "repo" holds headline facts. "sections" lists report \
sections in reading order; each has a "key", "counts", optional "facts" and \
"items". Each item has an "id", a "rule" (what was found), "facts", and its \
basis, confidence, severity and effort (S, M or L). "top" lists the ids of the \
three items to do first.

Return one JSON object and nothing else:
{"summary": "...", "sections": [{"key": "...", "intro": "...", \
"items": [{"id": "...", "text": "..."}]}]}

Lengths: summary 3 to 4 sentences, built around the "top" items. Each section \
intro 1 to 3 sentences. Each item 1 to 2 sentences. Write every section and \
every item you were given, in the order given.

Hard rules:
- Use only the facts given. Every number you write must appear in that item's \
facts (or, for an intro, in that section's counts or facts).
- Never write a file path, URL, command, code or markdown. Name a file only by \
the "file" value you were given.
- Never invent an item or an id. Copy ids exactly.
- Never describe a secret's value; say where it is and what to do.
- Do not reorder sections or items.

Style: say what to do first, why it matters and how big the job is. Second \
person. No em dashes. Never write "consider", "might want to", "perhaps" or \
"may".

Example item in:
{"id": "fl_3a91c0d2e4f1", "rule": "hardcoded_password", "file": \
"seed_admin.py", "facts": {"kind": "hardcoded_password", "locations": 1}, \
"basis": "measured", "confidence": "high", "severity": "act_now", "effort": "S"}
Its text out:
{"id": "fl_3a91c0d2e4f1", "text": "seed_admin.py has a password written \
into the script. Rotate it, then read it from the environment: deleting the line \
is not enough, because it stays in git history."}
"""

# Fact keys the prose never needs: commands a renderer prints from ids, ids
# themselves, and anything naming a person.
_DROP_FACTS = frozenset({"verify", "batch_action_id", "action_id", "freshness"})
_PERSON_FACTS = ("author", "email", "owner", "committer")

# A token shaped like a path or a file name: ``a/b.py``, ``a/b/c``, ``b.py``.
# One slash and no extension (``I/O``, ``and/or``) is words, not a path.
_PATH_TOKEN = re.compile(
    r"[\w.@~-]*(?:[/\\][\w.@~-]+)*[/\\][\w@~-]+\.[A-Za-z][A-Za-z0-9]{0,7}\b"
    r"|[\w.@~-]+(?:[/\\][\w.@~-]+){2,}"
    r"|\b[\w-]{2,}\.[A-Za-z][A-Za-z0-9]{0,7}\b"
    r"|[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
)
# A written number: ``16``, ``1,811``, ``8.28`` (in ``8.28x``), ``52`` (in ``52%``).
# Not one inside a word or a name (``run_10``, ``v2``); every digit run of a
# range or a fraction (``3-5000``, ``1/9999``) is checked.
_NUMBER = re.compile(r"(?<!\w)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?")

_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"<?(?:https?://|www\.)[^\s<>)]*[^\s<>).,;:!?]>?", re.IGNORECASE)
_FENCED = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_HTML_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
_EMPHASIS = re.compile(r"\*+|\|")
# Markdown block marks a line could open with: a heading, a quote, a list.
_BLOCK_MARK = re.compile(r"^\s*(?:[#>]+|[-+*]\s|\d+[.)]\s)\s*")
_EM_DASH = re.compile(r"\s*\u2014\s*")
_SPACES = re.compile(r"\s+")
_SPACE_BEFORE_MARK = re.compile(r"\s+([,.;:!?])(?=\s|$)")


@dataclass(frozen=True, slots=True)
class LlmOutcome:
    """What the one call did: the model, its spend, and what kept the template."""

    model: str
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    #: USD at the cost ledger's rates; ``None`` when the model is unpriced.
    cost_usd: float | None = None
    #: Why the whole report kept the template text; ``None`` when the model's was used.
    fallback_reason: str | None = None
    #: Piece -> why it kept the template text. A piece is an item id,
    #: ``summary`` or ``section:<key>``.
    fallbacks: Mapping[str, str] = field(default_factory=dict)
    #: Ids the model returned that it was never sent (dropped).
    unknown_ids: int = 0
    #: The reply stopped at :data:`MAX_TOKENS`.
    truncated: bool = False

    @property
    def used(self) -> bool:
        """Whether any of the model's text made it into the prose."""
        return self.fallback_reason is None

    @property
    def item_fallbacks(self) -> int:
        """Items sent to the model that kept their template text."""
        return sum(1 for key in self.fallbacks if not _is_block_key(key))

    def to_json(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "fallback_reason": self.fallback_reason,
            "fallbacks": dict(self.fallbacks),
            "item_fallbacks": self.item_fallbacks,
            "unknown_ids": self.unknown_ids,
            "truncated": self.truncated,
        }


def _is_block_key(key: str) -> bool:
    return key == "summary" or key.startswith("section:")


def first_look_model(provider: Any, model: str | None = None) -> str:
    """The model the call runs on: *model* (``report.first_look.model``), else the
    provider's own.

    Ceiling: no provider names a cheaper model for one-off calls, so an empty
    setting runs on the provider's configured model. Upgrade path: a cheap-model
    field on ``ProviderSpec``, read here.
    """
    return model or str(getattr(provider, "model_name", "") or "")


def _provider_for(provider: Any, model: str | None, repo_root: Path | str | None) -> Any:
    """*provider*, or the same provider built for *model* when that differs.

    Built through the one resolution path (key, base URL, repo config) and
    recording on *provider*'s cost tracker.
    """
    if not model or model == getattr(provider, "model_name", None):
        return provider
    from repowise.core.providers import get_provider
    from repowise.core.providers.llm.resolve import llm_provider_kwargs, resolve_llm

    name = provider.provider_name
    selection = resolve_llm(repo_root, provider=name, model=model, autodetect=False)
    sibling = get_provider(name, **llm_provider_kwargs(selection, repo_root))
    sibling._cost_tracker = getattr(provider, "_cost_tracker", None)
    return sibling


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


def _basename(path: str) -> str:
    return basename(path.replace("\\", "/").rstrip("/"))


def _short_path(match: re.Match[str]) -> str:
    token = match.group(0)
    return _basename(token) if "/" in token or "\\" in token else token


def _fact_value(value: Any) -> Any:
    """*value* as the model may see it: every path in it becomes its basename."""
    if isinstance(value, str):
        return _PATH_TOKEN.sub(_short_path, value)
    if isinstance(value, Mapping):
        return _facts(value)
    if isinstance(value, list | tuple):
        return [_fact_value(v) for v in value]
    return value


def _facts(facts: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in facts.items():
        if value is None or key in _DROP_FACTS:
            continue
        lowered = str(key).lower()
        if any(p in lowered for p in _PERSON_FACTS) and not isinstance(value, int | float):
            continue
        out[str(key)] = _fact_value(value)
    return out


def _item_input(item: Item) -> dict[str, Any]:
    data: dict[str, Any] = {"id": item.id, "rule": item.rule}
    if item.locations:
        data["file"] = _basename(item.locations[0].path)
    data["facts"] = _facts(item.title_facts)
    total = max(item.locations_total, len(item.locations))
    if total > 1:
        data["locations"] = total
    data.update(
        basis=item.basis, confidence=item.confidence, severity=item.severity, effort=item.effort
    )
    return data


def _section_input(block: SectionBlock) -> dict[str, Any]:
    shown = block.items[:SECTION_CAP]
    data: dict[str, Any] = {"key": block.key, "counts": {"items": len(block.items)}}
    if len(block.items) > len(shown):
        data["counts"]["shown"] = len(shown)
    facts = _facts(block.facts)
    if facts:
        data["facts"] = facts
    data["items"] = [_item_input(i) for i in shown]
    return data


def build_input(report: FirstLookReport) -> dict[str, Any]:
    """The one user message: facts, counts and basenames, no source text.

    Each section sends the items a page shows (:data:`SECTION_CAP`); the rest
    keep their template text, so the call stays one size on any repo.
    """
    repo = {k: v for k, v in report.repo.items() if k in ("name", "branch", "files", "health")}
    repo["commit"] = (report.stamp.commit or "")[:12] or None
    if report.repo.get("headline_facts"):
        repo["headline_facts"] = [_facts(f) for f in report.repo["headline_facts"]]
    return {
        "repo": _facts(repo),
        "top": list(report.top),
        "sections": [_section_input(s) for s in report.sections],
    }


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Allowed:
    """What one piece of prose may name: numbers and path tokens it was sent,
    and the full paths of its locations."""

    numbers: frozenset[float]
    tokens: frozenset[str]
    paths: tuple[str, ...] = ()


def _numbers(text: str) -> Iterable[tuple[float, int]]:
    """Each written number and how many decimals it was written with."""
    for match in _NUMBER.finditer(text):
        whole, frac = match.group(1).replace(",", ""), match.group(2)
        yield float(f"{whole}.{frac}" if frac else whole), len(frac or "")


def _allowed(sent: Iterable[Any], paths: Iterable[str] = ()) -> _Allowed:
    blob = json.dumps(list(sent), ensure_ascii=False)
    numbers: set[float] = set()
    for value, _ in _numbers(blob):
        numbers.add(value)
        if 0 < value <= 1:
            numbers.add(round(value * 100, 6))  # a share written as a percentage
    tokens = {m.group(0) for m in _PATH_TOKEN.finditer(blob)}
    return _Allowed(frozenset(numbers), frozenset(tokens), tuple(paths))


def _without_ids(data: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k != "id"}


def clean_text(text: str) -> str:
    """Model text as plain text: no markdown links, URLs, code, HTML or em dashes."""
    text = _FENCED.sub(" ", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub(" ", text)
    text = _HTML_TAG.sub(" ", text)
    text = text.replace("`", "")
    text = _EMPHASIS.sub("", text)
    text = _EM_DASH.sub(", ", text)
    text = _SPACE_BEFORE_MARK.sub(r"\1", _SPACES.sub(" ", text)).strip()
    return _BLOCK_MARK.sub("", text)


def _number_ok(text: str, allowed: _Allowed) -> bool:
    for value, decimals in _numbers(text):
        if not any(round(a, decimals) == value for a in allowed.numbers):
            return False
    return True


def _paths_ok(text: str, allowed: _Allowed) -> bool:
    for match in _PATH_TOKEN.finditer(text):
        token = match.group(0).rstrip(".")
        if token in allowed.tokens:
            continue
        posix = token.replace("\\", "/")
        if any(p == posix or p.endswith("/" + posix) for p in allowed.paths):
            continue
        return False
    return True


def _guard(raw: Any, allowed: _Allowed, piece: str) -> tuple[str | None, str | None]:
    """``(text, None)`` when *raw* passes every guard, else ``(None, reason)``."""
    from repowise.core.analysis.security.credentials import holds_secret

    if not isinstance(raw, str):
        return None, "missing"
    if holds_secret(raw):
        log.warning("first_look_secret_guard", piece=piece)
        return None, "secret"
    text = clean_text(raw)
    if not text:
        return None, "empty"
    if not _number_ok(text, allowed):
        return None, "number"
    if not _paths_ok(text, allowed):
        return None, "path"
    return text, None


# ---------------------------------------------------------------------------
# Reply
# ---------------------------------------------------------------------------


_ITEM_START = re.compile(r'\{\s*"id"\s*:')
_JSON_STRING = r'("(?:[^"\\]|\\.)*")'
_SUMMARY = re.compile(r'"summary"\s*:\s*' + _JSON_STRING)
_INTRO = re.compile(r'"key"\s*:\s*"([^"\\]+)"\s*,\s*"intro"\s*:\s*' + _JSON_STRING)


def _parse_reply(content: str) -> dict[str, Any] | None:
    """The reply as ``{summary, sections: {key: intro}, items: {id: text}}``;
    ``None`` when it is not the asked-for shape."""
    from repowise.core.generation.concept_tree.naming import parse_json_object

    data = parse_json_object(content)
    sections = data.get("sections")
    if not isinstance(sections, list) or not all(isinstance(s, Mapping) for s in sections):
        return None
    intros: dict[str, Any] = {}
    items: dict[str, Any] = {}
    for section in sections:
        if isinstance(section.get("key"), str):
            intros[section["key"]] = section.get("intro")
        for entry in section.get("items") or ():
            if isinstance(entry, Mapping) and isinstance(entry.get("id"), str):
                items[entry["id"]] = entry.get("text")
    return {"summary": data.get("summary"), "sections": intros, "items": items}


def _salvage(content: str) -> dict[str, Any]:
    """The complete pieces of a reply cut off mid-JSON."""
    decoder = json.JSONDecoder()
    items: dict[str, Any] = {}
    for match in _ITEM_START.finditer(content):
        try:
            entry, _ = decoder.raw_decode(content, match.start())
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            items[entry["id"]] = entry.get("text")
    intros = {m.group(1): _string(m.group(2)) for m in _INTRO.finditer(content)}
    summary = _SUMMARY.search(content)
    return {
        "summary": _string(summary.group(1)) if summary else None,
        "sections": intros,
        "items": items,
    }


def _string(literal: str) -> str | None:
    try:
        return json.loads(literal)
    except ValueError:
        return None


def _cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """USD at the cost ledger's rates, the figure ``repowise status`` adds up."""
    from repowise.core.generation.cost_tracker import resolve_model_pricing

    rates = resolve_model_pricing(model)
    if rates is None:
        return None
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


def _reasoning(provider: Any) -> str:
    """Low effort where the model offers it: the job is short prose, not thinking."""
    try:
        modes = provider.supported_reasoning_modes()
    except Exception:
        return "auto"
    return "low" if "low" in modes else "auto"


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------


async def _call(provider: Any, model: str | None, repo_root: Any, sent: dict[str, Any]) -> Any:
    """The one ``generate`` call, its spend recorded under :data:`OPERATION`."""
    runner = _provider_for(provider, model, repo_root)
    tracker = getattr(runner, "_cost_tracker", None)
    scope = (
        tracker.record_as(OPERATION)
        if tracker is not None and hasattr(tracker, "record_as")
        else contextlib.nullcontext()
    )
    with scope:
        return await runner.generate(
            SYSTEM_PROMPT,
            json.dumps(sent, ensure_ascii=False),
            max_tokens=MAX_TOKENS,
            temperature=0.2,
            reasoning=_reasoning(runner),
        )


def _read(content: str, truncated: bool) -> dict[str, Any] | None:
    """The reply's pieces; from a cut-off reply, the complete ones."""
    reply = _parse_reply(content)
    if reply is None and truncated:
        reply = _salvage(content)
        if not (reply["items"] or reply["sections"] or reply["summary"]):
            return None
    return reply


@dataclass
class _Merge:
    """Places each guarded piece of the reply into the prose; counts the rest."""

    prose: dict[str, Any]
    reply: Mapping[str, Any]
    missing: str
    fallbacks: dict[str, str] = field(default_factory=dict)
    used: int = 0

    def place(
        self, target: dict[str, Any], key: str, piece: str, raw: Any, allowed: _Allowed
    ) -> None:
        if raw is None:
            self.fallbacks[piece] = self.missing
            return
        text, reason = _guard(raw, allowed, piece)
        if text is None:
            self.fallbacks[piece] = reason or "missing"
            return
        target[key] = text
        self.used += 1

    def section(self, sent: Mapping[str, Any], block: SectionBlock) -> None:
        located = {i.id: i for i in block.items}
        section_sent = [_without_ids(sent), *(_without_ids(i) for i in sent["items"])]
        paths = (loc.path for i in block.items for loc in i.locations)
        intros = self.prose.setdefault("sections", {})
        intro = self.reply["sections"].get(block.key)
        self.place(intros, block.key, f"section:{block.key}", intro, _allowed(section_sent, paths))
        items = self.prose.setdefault("items", {})
        for entry in sent["items"]:
            allowed = _allowed(
                [_without_ids(entry)], [loc.path for loc in located[entry["id"]].locations]
            )
            self.place(
                items, entry["id"], entry["id"], self.reply["items"].get(entry["id"]), allowed
            )


async def rewrite_prose(
    report: FirstLookReport,
    provider: Any,
    *,
    template: Mapping[str, Any],
    model: str | None = None,
    repo_root: Path | str | None = None,
) -> tuple[dict[str, Any], LlmOutcome]:
    """The report's prose written by one call to *provider*, and what it cost.

    *template* is the template prose (``writer``, ``summary``, ``sections``,
    ``items``, ``setup``); every piece the model's text cannot replace keeps
    its template text, and on an unusable reply the whole template comes back.
    *model* overrides the provider's model (``report.first_look.model``).
    One call, never retried, whatever comes back.
    """
    prose = copy.deepcopy(dict(template))
    model_id = first_look_model(provider, model)
    meta = {"model": model_id, "provider": str(getattr(provider, "provider_name", "") or "")}
    sent = build_input(report)
    try:
        response = await _call(provider, model, repo_root, sent)
    except Exception as exc:
        # Broad on purpose: a report must never fail the run that writes it.
        log.warning("first_look_llm_failed", model=model_id, error=str(exc))
        return prose, LlmOutcome(**meta, fallback_reason="provider_error")

    tokens_in, tokens_out = int(response.input_tokens or 0), int(response.output_tokens or 0)
    truncated = response.stop_reason == "max_tokens"
    meta.update(
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        cost_usd=_cost(model_id, tokens_in, tokens_out),
        truncated=truncated,
    )
    reply = _read(response.content or "", truncated)
    if reply is None:
        reason = "truncated" if truncated else "unparseable"
        return prose, LlmOutcome(**meta, fallback_reason=reason)

    merge = _Merge(prose, reply, "truncated" if truncated else "missing")
    for section, block in zip(sent["sections"], report.sections, strict=True):
        merge.section(section, block)
    every_path = (loc.path for i in report.items() for loc in i.locations)
    merge.place(prose, "summary", "summary", reply["summary"], _allowed([sent], every_path))

    sent_ids = {i["id"] for s in sent["sections"] for i in s["items"]}
    meta.update(
        fallbacks=merge.fallbacks,
        unknown_ids=sum(1 for key in reply["items"] if key not in sent_ids),
    )
    if not merge.used:
        return prose, LlmOutcome(**meta, fallback_reason="no_usable_text")
    prose["writer"] = model_id
    return prose, LlmOutcome(**meta)


__all__ = [
    "MAX_TOKENS",
    "OPERATION",
    "SYSTEM_PROMPT",
    "LlmOutcome",
    "build_input",
    "clean_text",
    "first_look_model",
    "rewrite_prose",
]
