"""One packet per update: which spans go in front of the model, and how.

The packet is whole sessions of new prose, oldest first, bounded by a session
count and an input-token budget. It is deliberately not a cue-word summary:
the ablation behind this design recovered about 5 of 16 themes for 40% of the
tokens, so compaction saves the wrong thing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from repowise.core.analysis.decisions.discovery.spans import ProseSpan

__all__ = [
    "PROMPT_TEMPLATE",
    "SYSTEM_PROMPT",
    "DiscoveryPacket",
    "build_packet",
    "packet_tokens",
]

#: Characters per token. Deliberately not
#: :func:`repowise.core.distill.budget.estimate_tokens`, which floors at 4 on
#: purpose because every distill savings figure is reported on that one scale.
#: This number bounds a request rather than reporting on one, so it rounds the
#: other way: transcript prose full of paths, punctuation and code fragments
#: tokenizes well below the prose rule of thumb, and 4 undercounted a measured
#: packet by 21%.
_CHARS_PER_TOKEN = 3

#: Room the instructions and the trailing known-paths list take, in tokens.
_OVERHEAD_TOKENS = 900

#: What one span costs beyond its text: the id, role and file header around it.
#: Charged per span because a packet of many short turns is mostly header, and
#: pricing only the bodies is what overran the budget.
_SPAN_OVERHEAD_TOKENS = 16


def packet_tokens(text: str) -> int:
    """A pessimistic token ceiling for *text*, for bounding one request."""
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


SYSTEM_PROMPT = (
    "You are auditing coding-agent sessions for durable repository decisions. "
    "Precision matters more than recall. A durable decision is a constraint, "
    "chosen design, tradeoff, rejected approach, or operating rule that should "
    "affect future work in this repository. Do not extract the user's immediate "
    "task, status updates, generic engineering advice, assistant suggestions "
    "that were never accepted, or temporary branch and worktree instructions. "
    "Return only valid JSON. Never invent rationale that is not in the spans."
)

PROMPT_TEMPLATE = """\
Below are numbered prose spans from recent coding-agent sessions in one \
repository. Each span is one turn, tagged with its span id and the files that \
turn's tools touched.

{spans_block}

Known file paths for this packet (you may cite these and no others):
{paths_block}

Return every durable decision the spans establish, as a JSON object:
{{
  "candidates": [
    {{
      "title": "short imperative title",
      "decision": "the rule or choice, grounded in the cited spans",
      "rationale": "why, only if a span states it; empty string otherwise",
      "kind": "constraint | design_choice | tradeoff | rejected_approach | operating_rule",
      "durability": "durable | task_local",
      "acceptance_basis": "user_explicit | user_correction | implemented_and_validated",
      "evidence_quote": "one sentence copied verbatim from a cited span",
      "span_ids": ["the span ids this rests on"],
      "paths": ["only paths from the known list above"]
    }}
  ],
  "rejected_task_local": 0,
  "rejected_assistant_only": 0
}}

Rules:
- One claim per candidate. If a moment settles two independent things, return \
two candidates.
- Reject anything the assistant merely proposed and the user never accepted, \
anything that was only a plan for that task, and anything whose scope ends \
with the session. Count those in the rejected counters.
- The evidence quote must appear verbatim in one of the spans you cite.
- Do not invent file paths. An empty "paths" list is correct when the spans \
name none.
- Return {{"candidates": [], "rejected_task_local": 0, \
"rejected_assistant_only": 0}} when nothing qualifies.
"""


@dataclass(frozen=True, slots=True)
class DiscoveryPacket:
    """The spans one call will see, plus what it is allowed to cite."""

    spans: tuple[ProseSpan, ...]
    known_paths: tuple[str, ...]
    prompt: str
    estimated_tokens: int
    #: Sessions represented, for the funnel counters.
    sessions: tuple[str, ...] = field(default=())

    def __bool__(self) -> bool:
        return bool(self.spans)


def _span_cost(span: ProseSpan) -> int:
    return (
        packet_tokens(span.text)
        + packet_tokens(", ".join(span.files[:8]))
        + _SPAN_OVERHEAD_TOKENS
    )


def _spans_block(spans: tuple[ProseSpan, ...]) -> str:
    parts: list[str] = []
    current: str | None = None
    for span in spans:
        if span.session_id != current:
            current = span.session_id
            parts.append(f"\n===== session {current} =====")
        files = ", ".join(span.files[:8]) or "(none)"
        parts.append(f"[span {span.span_id}] {span.role} · files: {files}\n{span.text}")
    return "\n".join(parts)


def build_packet(
    spans: list[ProseSpan],
    *,
    max_sessions: int,
    max_input_tokens: int,
) -> DiscoveryPacket:
    """Take whole sessions off the front of the queue until a bound is hit.

    Sessions are admitted whole so a candidate is never grounded in half a
    conversation. A session too large to fit on its own is admitted anyway,
    span-truncated, rather than wedging the queue head forever.
    """
    budget = max(0, max_input_tokens - _OVERHEAD_TOKENS)
    chosen: list[ProseSpan] = []
    sessions: list[str] = []
    spent = 0
    by_session: dict[str, list[ProseSpan]] = {}
    for span in spans:
        by_session.setdefault(span.session_id, []).append(span)

    for session_id, session_spans in by_session.items():
        if len(sessions) >= max_sessions:
            break
        cost = sum(_span_cost(span) for span in session_spans)
        if spent and spent + cost > budget:
            break
        if not spent and cost > budget:
            session_spans = _fit(session_spans, budget)
            cost = sum(_span_cost(span) for span in session_spans)
        sessions.append(session_id)
        chosen.extend(session_spans)
        spent += cost

    # The per-session estimate is additive and the rendered prompt is not, so
    # the packet is measured and trimmed until it fits. The budget is a ceiling
    # the user set, not a target to land near.
    while True:
        packet = _render(tuple(chosen), tuple(sessions))
        if packet.estimated_tokens <= max_input_tokens or len(chosen) <= 1:
            # One span always survives, even over budget. Collection clips a
            # span to _SPAN_CHARS, so in practice it fits; refusing to send it
            # would wedge the queue head on the one item nothing can drain.
            return packet
        if len(sessions) > 1:
            dropped = sessions.pop()
            chosen = [span for span in chosen if span.session_id != dropped]
        else:
            chosen.pop()


def _render(spans: tuple[ProseSpan, ...], sessions: tuple[str, ...]) -> DiscoveryPacket:
    """One packet, with the known-node set its spans are allowed to cite.

    That set is the files those turns' tools actually touched, already
    repo-relative and repo-scoped at collection. The model may select from it;
    it may not name a path of its own.
    """
    cited = sorted({path for span in spans for path in span.files})
    prompt = PROMPT_TEMPLATE.format(
        spans_block=_spans_block(spans),
        paths_block="\n".join(f"- {path}" for path in cited) or "(none)",
    )
    return DiscoveryPacket(
        spans=spans,
        known_paths=tuple(cited),
        prompt=prompt,
        estimated_tokens=packet_tokens(prompt),
        sessions=sessions,
    )


def _fit(spans: list[ProseSpan], budget: int) -> list[ProseSpan]:
    """The longest prefix of one session's spans that fits *budget*."""
    kept: list[ProseSpan] = []
    spent = 0
    for span in spans:
        cost = _span_cost(span)
        if kept and spent + cost > budget:
            break
        kept.append(span)
        spent += cost
    return kept
