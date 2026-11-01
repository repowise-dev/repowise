"""The README's first prose paragraph: what the repository says it is.

Used where one sentence of the project's own framing is wanted (the C4
system context). The repository overview reads the README in full sections
instead (``generation.onboarding.docs``).
"""

from __future__ import annotations

import re
from pathlib import Path

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
#: A list item that is only a link: a table-of-contents entry, not prose.
_TOC_ENTRY = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[[^\]]+\]\([^)]*\)\s*$")


def _opening_paragraph(lines: list[str], start: int, *, past_headings: bool = False) -> str:
    """The first prose paragraph at or after *start*, before the next heading.

    Skips fenced code, decoration and blank lines. Returns "" when the section
    opens straight onto another heading, which is the common shape of a table
    of contents, unless *past_headings* lets it read on past headings and
    link-only list items to the first prose.
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
            if body or not past_headings:
                break
            i += 1
            continue
        if not stripped or _DECORATION.match(line) or (past_headings and _TOC_ENTRY.match(line)):
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


def readme_opening(repo_root: Path) -> str:
    """The README's first prose paragraph, headed or not; "" when there is none."""
    for rel in _DIGEST_DOCS:
        try:
            text = (repo_root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        paragraph = _opening_paragraph(text.splitlines(), 0, past_headings=True)
        if paragraph:
            return paragraph
    return ""


#: A module page reads the README beside its code. Smaller than the front
#: page's cap: it sits beside code excerpts that carry the mechanism.
_MODULE_README_NAMES = ("README.md", "README.rst", "readme.md")
_MODULE_README_CHARS = 2000
_MODULE_README_TOTAL = 6000


def module_readmes(repo_root: Path | str | None, directories: list[str | None]) -> list[dict]:
    """READMEs in *directories*, shallowest first, each cut to its head. Never raises.

    ``[{"path", "text"}]``; badges, images and link targets are dropped so the
    cap is spent on the maintainers' sentences. The repository root's README is
    the front page's, so an empty directory is skipped.
    """
    if repo_root is None:
        return []
    out: list[dict] = []
    budget = _MODULE_README_TOTAL
    for directory in sorted({d for d in directories if d}, key=lambda d: (d.count("/"), d)):
        text, rel = _first_readme(Path(repo_root), directory)
        if not text:
            continue
        kept = [_LINK.sub(_LINK_TEXT, _IMAGE.sub("", ln)) for ln in text.splitlines()]
        clean = re.sub(r"\n{3,}", "\n\n", "\n".join(ln for ln in kept if not _DECORATION.match(ln)))
        clean = clean.strip()[: min(_MODULE_README_CHARS, budget)]
        if clean:
            out.append({"path": rel, "text": clean})
            budget -= len(clean)
        if budget <= 0:
            break
    return out


def _first_readme(repo_root: Path, directory: str) -> tuple[str, str]:
    for name in _MODULE_README_NAMES:
        rel = f"{directory}/{name}"
        try:
            return (repo_root / rel).read_text(encoding="utf-8", errors="replace"), rel
        except OSError:
            continue
    return "", ""
