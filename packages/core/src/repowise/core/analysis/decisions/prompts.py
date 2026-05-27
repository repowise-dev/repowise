"""LLM prompt templates for the decision extractor.

Extracted from the former decision_extractor.py; imported back into
``extractor.py``. Internal to the decisions package.
"""

from __future__ import annotations

_SYSTEM_PROMPT = (
    "You are an architectural decision extractor. "
    "You extract structured decision records from code context. "
    "Return only valid JSON. Never invent rationale not present in the source."
)

INLINE_MARKER_PROMPT = """\
A developer left architectural decision markers in their code. \
Extract each one as a structured decision record.

Markers found in file: {file_path}

{markers_block}

For each marker, return a JSON object:
{{
  "title": "short title of the decision",
  "context": "what situation forced this",
  "decision": "what was chosen",
  "rationale": "why",
  "alternatives": ["rejected alternatives if mentioned"],
  "consequences": ["tradeoffs if mentioned"],
  "tags": ["relevant tags from: auth, database, api, performance, security, infra, testing"]
}}

Return a JSON array of decision objects. If a marker is not an architectural \
decision, skip it. Return [] if none qualify.
"""

GIT_ARCHAEOLOGY_PROMPT = """\
Analyze these git commits to determine if they represent architectural decisions.

{commits_block}

For each commit that IS an architectural decision (a deliberate choice about \
system structure, patterns, migrations, or technology), return a JSON object:
{{
  "commit_sha": "the sha",
  "title": "short title",
  "context": "what situation forced this",
  "decision": "what was chosen or changed",
  "rationale": "why this approach (infer from message and files only)",
  "alternatives": [],
  "consequences": [],
  "tags": ["relevant tags"]
}}

Return a JSON array. Skip commits that are just bug fixes or minor changes. \
Return [] if none qualify. Do not hallucinate rationale.
"""

README_MINING_PROMPT = """\
Analyze this documentation file and extract any architectural decisions.

File: {file_path}
Content:
{content}

Look for:
- Technology choices and why ("We use X because Y")
- Things replaced or migrated away from
- Explicit architectural constraints
- Design patterns chosen and why

Return a JSON array of decisions:
{{
  "title": "short title",
  "context": "situation that forced it",
  "decision": "what was chosen",
  "rationale": "why",
  "alternatives": [],
  "consequences": [],
  "tags": [],
  "source_quote": "exact quote from the document"
}}

Only extract explicit decisions. Return [] if none found.
"""

CHANGELOG_MINING_PROMPT = """\
These are CHANGELOG entries from sections that usually record deliberate \
changes (Changed / Removed / Deprecated). Extract any that represent an \
architectural decision (a technology/pattern change, a removal, a deprecation \
with a reason).

{entries_block}

Return a JSON array of decisions:
{{
  "title": "short title",
  "context": "what prompted it (only if stated)",
  "decision": "what was changed/removed/deprecated",
  "rationale": "why (only if stated — do not invent)",
  "alternatives": [],
  "consequences": [],
  "tags": [],
  "source_quote": "the exact changelog line this came from"
}}

Skip pure feature additions and trivial fixes. Return [] if none qualify.
"""

PR_BODY_MINING_PROMPT = """\
These are squash-merge / PR commit bodies. Extract any architectural decision \
described in them (technology choices, migrations, structural changes, things \
deliberately rejected).

{bodies_block}

For each decision return a JSON object:
{{
  "commit_sha": "the sha shown",
  "title": "short title",
  "context": "what situation forced this (only if stated)",
  "decision": "what was chosen or changed",
  "rationale": "why (quote/paraphrase the body — never invent)",
  "alternatives": ["rejected alternatives if mentioned"],
  "consequences": [],
  "tags": [],
  "source_quote": "the exact sentence from the body this came from"
}}

Return a JSON array. Skip bodies that are just checklists or release noise. \
Return [] if none qualify.
"""

COMMENT_ARCHAEOLOGY_PROMPT = """\
These are block comments / docstrings from high-centrality code (the files \
most other code depends on). Extract any architectural decision whose \
rationale is explained in the prose.

{comments_block}

For each decision return a JSON object:
{{
  "title": "short title",
  "context": "what situation forced this (only if stated)",
  "decision": "what was chosen",
  "rationale": "why (quote/paraphrase the comment — never invent)",
  "alternatives": [],
  "consequences": [],
  "tags": [],
  "source_quote": "the exact sentence from the comment this came from"
}}

Only extract decisions whose reasoning is actually written in the prose. \
Return [] if none qualify.
"""
