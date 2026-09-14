"""What the repository says it is, in its own sentences, for the front page.

The repo overview's payload is otherwise entirely structural: pagerank,
communities, package file counts, entry-point paths. A model handed only that
can describe a directory tree accurately and never say what the product is
for, because nothing it was given says so. The words are not missing from the
repository — the intelligence layers, the store architecture, the task-oriented
tools are all under headings in its own README. No generation prompt had ever
read it.

**The measured reason this is a keyhole and not a pipe.**
``concept_tree/vocabulary.py`` records that nine thousand characters of README
handed to the outline *planner* collapsed coverage from 23 pages / 87.5% to 3
pages / 5.4%, and invented a docs path matching the marketing language rather
than the tree. That is a **selection** task: prose outvoted structure at
choosing what exists. The overview is a **writing** task that selects nothing,
so the finding transfers as a bound rather than a veto. Two properties keep it
one:

* **Capped well under the collapse.** ``_MAX_CHARS`` is 2,500 against the 9,000
  that was measured to collapse a planner. The cap is the guardrail, not a
  token budget, which is why it is a constant here and not a config value.
* **Headings and openers only.** A heading names a thing and its first
  paragraph says what the thing is; the rest of a section is instructions,
  tables and examples. Reading only the openers is what keeps 60KB of README
  inside the cap without choosing which 2,500 characters by relevance, which
  would be a selection decision made on prose.

Structure stays the sole authority on paths, counts and package names. This
supplies vocabulary and framing; the template says so in as many words.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Well under the 9,000 characters measured to collapse the planner, and
#: roughly the size of the structural payload it sits beside, so neither one
#: dominates the prompt.
_MAX_CHARS = 2500

#: Root-anchored, and only these two. A ``docs/*.md`` glob matches a path tail,
#: which is how vendored and scratch documents reached the glossary once
#: already; see ``concept_tree/vocabulary.py``.
_DIGEST_DOCS = ("README.md", "docs/README.md")

#: Headings down to level three. Deeper ones are steps and options inside a
#: section, not names of things.
_HEADING = re.compile(r"^\s{0,3}(#{1,3})\s+(.+?)\s*#*\s*$")

#: A line that is only badges, an image, raw HTML, or a link-only banner. These
#: open most READMEs and say nothing, and left in they would spend the cap
#: before the first sentence.
_DECORATION = re.compile(r"^\s*(?:!\[|<[a-zA-Z/!]|\[!\[|\||-{3,}|={3,}|\*{3,})")

#: Inline images and the link syntax around their targets. The link *text* is
#: prose and stays; the URL is not.
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
#: Named because a backslash cannot appear inside an f-string before 3.12.
_LINK_TEXT = r"\1"


def _opening_paragraph(lines: list[str], start: int) -> str:
    """The first prose paragraph at or after *start*, before the next heading.

    Skips fenced code, decoration and blank lines. Returns "" when the section
    opens straight onto another heading, which is the common shape of a table
    of contents.
    """
    i = start
    fenced = False
    body: list[str] = []
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        # Fence state is read before anything else, because a `#` comment on
        # the first line of a shell block is not a heading and a `---` inside
        # a YAML example is not a rule.
        if stripped.startswith("```"):
            fenced = not fenced
            i += 1
            continue
        if fenced:
            i += 1
            continue
        if _HEADING.match(line):
            break
        if not stripped or _DECORATION.match(line):
            # Blank lines before the paragraph are skipped; one after it ends
            # the paragraph, so that a section contributes one paragraph.
            if body:
                break
            i += 1
            continue
        body.append(stripped)
        i += 1
    text = " ".join(body)
    text = _IMAGE.sub("", text)
    return _LINK.sub(_LINK_TEXT, text).strip()


def readme_digest(repo_root: Path, *, max_chars: int = _MAX_CHARS) -> str:
    """The repository's headings and section openers, capped. Never raises.

    Returns markdown: each heading kept at its own level with its opening
    paragraph beneath it. Empty when there is no README, so the caller can
    drop the whole block rather than label an absence.
    """
    out: list[str] = []
    budget = max_chars
    for rel in _DIGEST_DOCS:
        path = repo_root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        fenced = False
        for i, line in enumerate(lines):
            # A `# clone the repo` inside a bash block is a comment, and most
            # READMEs open with one. Read as a heading it becomes a name the
            # project supposedly gives one of its own parts.
            if line.strip().startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                continue
            match = _HEADING.match(line)
            if not match:
                continue
            hashes, title = match.groups()
            entry = f"{hashes} {_LINK.sub(_LINK_TEXT, title).strip()}"
            paragraph = _opening_paragraph(lines, i + 1)
            if paragraph:
                entry = f"{entry}\n{paragraph}"
            cost = len(entry) + 2
            if cost > budget:
                # A cut mid-document, not a ranked selection: the cap has to
                # bind on prose we did not choose, or the choosing becomes the
                # thing the planner's collapse warned about.
                return "\n\n".join(out)
            out.append(entry)
            budget -= cost
    return "\n\n".join(out)
