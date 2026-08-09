"""The one path from "we could replace this tool result" to a ledgered saving.

Three surfaces now replace a tool result instead of talking about it: the Read
skeleton, the Grep flood digest, and the re-read collapse. They differ only in
what they build. Everything around that build is identical, and was written
twice before this module existed:

1. **Can this harness honour a replacement at all**, protocol *and* installed
   build (:func:`can_replace`).
2. **Did this repo opt in**, fail-closed (``_shared.hook_flag_enabled``).
3. **Build the wire payload from the tool's own live response**
   (:func:`wire_payload`), never from a constructed envelope.
4. **No payload, no ledger row.** ``updatedToolOutput`` is validated against
   the replaced tool's *own* output schema, and a rejected replacement fails
   invisibly: exit 0, no stderr, the agent gets the original bytes, and the
   ledger records a saving that never happened. That failure has shipped here
   before and no test caught it, because a test can only prove the hook wrote
   the field, not that the harness accepted it. So the payload is built
   *before* the row is written, and a payload we cannot build cancels the row.
5. **A saving write, or a forgone-saving write, never both** — and the forgone
   leg answers the same questions the real one did, step 4 included, or it is
   measuring a feature we could not have shipped.

:func:`offer` is those five steps in order, with the surface supplying only
step 3's candidate. A fourth replacing surface should be a builder function
and nothing else.

**One rule changed on unification, deliberately.** The Read surface used to
record a *forgone* saving for a client that could not have honoured a
replacement in the first place. The digest never did, and the digest is right:
a saving is only forgone if the flag was the thing standing in the way. A
counterfactual measured under conditions the real path could not have met is
measuring a different feature.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ._shared import hook_flag_enabled
from ._shared import record_forgone as _record_forgone
from ._shared import record_saving as _record_saving

if TYPE_CHECKING:  # pragma: no cover - hot path keeps these out of the graph
    from pathlib import Path

    from repowise.cli.agent_adapters import AgentAdapter


class Offer:
    """A candidate replacement, and everything the two ledgers need to bill it.

    A small value object rather than a tuple: the caller records a saving, logs
    an efficacy row and emits the text, and four positional elements at that
    call site would read as noise. ``__slots__`` because this is the hot path.
    """

    __slots__ = ("category", "filter_name", "key", "new_tokens", "payload", "raw_tokens", "text")

    def __init__(
        self,
        *,
        key: str,
        text: str,
        raw_tokens: int,
        new_tokens: int,
        category: str,
        filter_name: str,
    ) -> None:
        #: What was replaced, for the ledger's ``node_id`` and the savings
        #: ledger's ``command`` column: a repo-relative path, or a pattern.
        self.key = key
        #: The replacement text, exactly as the agent will receive it.
        self.text = text
        #: What the agent would have been billed, and what it is billed now.
        #: Both are chars/4 estimates and both include any header, because the
        #: header is part of what arrives.
        self.raw_tokens = raw_tokens
        self.new_tokens = new_tokens
        #: Efficacy-ledger category (``skeleton_served``, ``digest_served``,
        #: ``reread_collapsed``) and savings-ledger filter name.
        self.category = category
        self.filter_name = filter_name
        #: Filled in by :func:`offer` once it has the live ``tool_response``.
        self.payload: dict | None = None

    @property
    def saved_tokens(self) -> int:
        return max(0, self.raw_tokens - self.new_tokens)


def can_replace(adapter: AgentAdapter) -> bool:
    """True when this harness can honour ``updatedToolOutput`` right now.

    Two questions, both the adapter's: does the protocol have a field that
    replaces a tool result, and does the *installed build* implement it. A
    surface that asked only the first would serve replacements into a client
    that drops them silently; one that asked only the second would assume
    every harness has the field. Neither is a question a Read handler should
    be answering by client name.
    """
    return bool(adapter.replaces_tool_output) and adapter.supports_updated_output()


def _as_read_output(tool_response: dict, text: str) -> dict | None:
    """Read's shape: ``{"type", "file": {"filePath", "content", "numLines", …}}``.

    Handing Read a bare string is rejected with ``does not match Read's output
    shape``. Built from the response's own keys so unknown or future ones carry
    through untouched and only ``content`` and the line counts move.
    ``totalLines`` is deliberately left alone: it describes the file, not this
    rendering of it, and it stays true after the swap.
    """
    file_block = tool_response.get("file")
    if not isinstance(file_block, dict) or "content" not in file_block:
        return None
    lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    return {**tool_response, "file": {**file_block, "content": text, "numLines": lines,
                                      "startLine": 1}}


def _as_grep_output(tool_response: dict, text: str) -> dict | None:
    """Grep's content mode. ``files_with_matches`` and Glob carry no content.

    ``totalLines`` and ``numFiles`` are left alone for the same reason Read's
    ``totalLines`` is: they describe the search, not the rendering of it, and
    they stay true after the swap. The digest's header restates both anyway.
    """
    if tool_response.get("mode") != "content":
        return None
    if not isinstance(tool_response.get("content"), str):
        return None
    lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    return {**tool_response, "content": text, "numLines": lines}


#: Wire-payload builder per tool name. A tool absent from this table cannot be
#: replaced, which is the safe default: ``updatedToolOutput`` is validated
#: against the replaced tool's own schema, so guessing an envelope for an
#: unknown tool produces exactly the invisible rejection step 4 exists to stop.
_PAYLOAD_BUILDERS: dict[str, Callable[[dict, str], dict | None]] = {
    "Read": _as_read_output,
    "Grep": _as_grep_output,
}


def wire_payload(tool_name: str, tool_response: object, text: str) -> dict | None:
    """*text* in the object shape this tool's output schema requires, or None.

    None degrades to no replacement rather than to a rejected one — the whole
    reason this is computed before the ledger row is written.
    """
    builder = _PAYLOAD_BUILDERS.get(tool_name)
    if builder is None or not isinstance(tool_response, dict):
        return None
    return builder(tool_response, text)


def offer(
    repo_path: Path,
    adapter: AgentAdapter,
    *,
    flag: str,
    source: str,
    tool_name: str,
    tool_response: object,
    build: Callable[[], Offer | None],
    forgone_gate: Callable[[], bool] | None = None,
) -> tuple[Offer | None, Callable[[], None] | None]:
    """``(served, on_emitted)`` for one replacement opportunity. Never raises.

    *build* is the surface's own candidate builder. It is called at most once,
    and **only on the leg that needs it now**: a client that cannot be served
    and a repo that has not opted in both pay nothing for finding out. *source*
    is the savings-ledger surface tag (``hook-read`` / ``hook-search``).

    ``served`` is the offer the agent will receive, with :attr:`Offer.payload`
    filled in, or None. ``on_emitted`` is the ledger write this owes, to run
    *after* the response is on its way: accounting must not sit between the
    agent and its tool result.

    The counterfactual needs that deferral more than the saving does, not
    less, which is why the whole of it — the build included — happens there.
    The real path has to build before responding, because what it builds *is*
    the response. This one hands the agent nothing, so making it wait for a
    build and a write lock would charge every qualifying call in an opted-out
    repo for a number it will never see.

    *forgone_gate* is the surface's own permission to spend that work: a
    once-per-key or per-session cap, claimed before the deferral because the
    claim usually has to land in state the caller is about to persist. A
    surface with no such cost to bound leaves it None.

    Everything is inside the try, gates included. A malformed config or a
    stale index must cost this one enrichment and nothing else the caller owes.
    """
    try:
        if not can_replace(adapter):
            return None, None
        if not hook_flag_enabled(repo_path, flag):
            if forgone_gate is not None and not forgone_gate():
                return None, None
            return None, _forgone_writer(
                repo_path, build, source, tool_name=tool_name, tool_response=tool_response
            )
        candidate = build()
        if candidate is None:
            return None, None
        payload = wire_payload(tool_name, tool_response, candidate.text)
        if payload is None:
            return None, None
        candidate.payload = payload
        return candidate, _saving_writer(repo_path, candidate, source)
    except Exception:
        return None, None


def _saving_writer(repo_path: Path, served: Offer, source: str) -> Callable[[], None]:
    """The savings-ledger write for a replacement the agent received."""

    def _write() -> None:
        try:
            _record_saving(
                repo_path,
                source=source,
                filter_name=served.filter_name,
                command=served.key,
                raw_tokens=served.raw_tokens,
                distilled_tokens=served.new_tokens,
            )
        except Exception:
            return

    return _write


def _forgone_writer(
    repo_path: Path,
    build: Callable[[], Offer | None],
    source: str,
    *,
    tool_name: str,
    tool_response: object,
) -> Callable[[], None]:
    """The write for a saving this repo *would* have made, had the flag been on.

    Builds the candidate here rather than up front — see :func:`offer` — so an
    opted-out repo learns what opting in is worth without any call waiting on
    the arithmetic. A candidate that comes back None forgoes nothing and writes
    nothing.

    **Step 4 applies here too.** The counterfactual has to answer the question
    the real path would have answered, and the real path refuses a replacement
    it cannot build a legal payload for. A forgone row written without that
    check credits the surface with a saving it would never have been allowed to
    make — the same class of error as counting one a client could not honour.

    A separate table from ``savings``, never the same one: every row there is
    an event that happened and ``repowise saved`` sums them into a published
    figure. Adding a hypothetical to that sum is the precise misreading the
    caveat under the report exists to prevent.
    """

    def _write() -> None:
        try:
            candidate = build()
            if candidate is None:
                return
            if wire_payload(tool_name, tool_response, candidate.text) is None:
                return
            _record_forgone(
                repo_path,
                source=source,
                path=candidate.key,
                raw_tokens=candidate.raw_tokens,
                distilled_tokens=candidate.new_tokens,
                filter_name=candidate.filter_name,
            )
        except Exception:
            return

    return _write
