"""Check that the marketing site's benchmark numbers still match docs/BENCHMARKS.md.

docs/BENCHMARKS.md is the methodology of record for every measured number
repowise publishes. The marketing site (a separate repo, checked out locally
at frontend/ but not part of this repo's git history) mirrors those numbers in
frontend/src/lib/benchmark-facts.ts so pages and blog posts can render them
from one place. Two copies of the same numbers drift; this script is what
catches it.

What it checks:
  1. Every row of every table in frontend's BENCHMARK_TABLES against the
     corresponding markdown table in docs/BENCHMARKS.md. A differing cell is
     a hard failure.
  2. Every scalar fact in frontend's BENCHMARK_FACTS: its `value` must appear,
     literally or via its numeric tokens, in the markdown section its
     `section` anchor names. This is a weaker check than table matching by
     design: it catches "31.6% became 29.8%" without requiring a parse of
     English prose.
  3. Every fact's `section` anchor must exist as a real heading in
     docs/BENCHMARKS.md, so the "how we measured" links on the site cannot
     404.
  4. Every fact with status "published" must carry a non-empty `caveat`.
  5. Facts with status "provisional" are flagged as warnings (not failures),
     so a run in flight cannot be silently forgotten.

What it cannot check (and says so in its output):
  - Prose facts whose value is not a number and does not appear verbatim in
    its cited section (a handful of narrative values like "15 of 15, then
    4 of 15..."). These are listed explicitly as unverifiable rather than
    silently passed.
  - Whether the *meaning* of a number is still correctly described (a value
    that matches by coincidence, or a caveat that has gone stale in spirit
    but not in wording).
  - Anything in frontend/ other than BENCHMARK_FACTS and BENCHMARK_TABLES in
    benchmark-facts.ts: this script does not scan pages or blog posts that
    might quote a number inline without going through the shared module.

This script parses TypeScript with a hand-rolled brace/bracket scanner, not a
real parser, and it deliberately does not attempt a general markdown-to-facts
parser either (see module docstring history in benchmark-facts.ts for why).
It is stdlib-only: no Node.js, no extra Python dependency.

frontend/ is a separate repo not checked into this one, so it is absent in
this repo's own CI. When the default frontend path does not exist, this
script prints a message and exits 0 rather than failing the build.

Usage:
    python scripts/check_benchmark_facts.py
    python scripts/check_benchmark_facts.py --benchmarks docs/BENCHMARKS.md --facts-ts frontend/src/lib/benchmark-facts.ts
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ─── TypeScript object-literal scanning ─────────────────────────────────────


def extract_balanced(text: str, start_idx: int) -> int:
    """Return the index of the bracket that closes text[start_idx].

    Handles nested {}/[] and skips over string literals (', ", `) so that a
    stray bracket character inside a quoted value cannot desync the count.
    """
    pairs = {"{": "}", "[": "]"}
    opener = text[start_idx]
    if opener not in pairs:
        raise ValueError(f"not an opening bracket at {start_idx}: {opener!r}")
    stack = [pairs[opener]]
    i = start_idx + 1
    in_string: str | None = None
    n = len(text)
    while i < n:
        c = text[i]
        if in_string:
            if c == "\\":
                i += 2
                continue
            if c == in_string:
                in_string = None
        elif c in ("'", '"', "`"):
            in_string = c
        elif c in pairs:
            stack.append(pairs[c])
        elif c in ("}", "]"):
            if not stack or stack[-1] != c:
                raise ValueError(f"mismatched bracket at {i}")
            stack.pop()
            if not stack:
                return i
        i += 1
    raise ValueError("unterminated bracket, reached end of text")


def strip_comments(block: str) -> str:
    """Strip //-to-end-of-line comments, string-aware.

    A naive `//.*` regex corrupts any string literal that contains a literal
    "//", which real entries in this file do: `source: "https://..."`. So
    this walks the text tracking whether it is inside a quote and only
    treats "//" as a comment start outside of one.
    """
    out: list[str] = []
    i = 0
    n = len(block)
    in_string: str | None = None
    while i < n:
        c = block[i]
        if in_string:
            out.append(c)
            if c == "\\":
                if i + 1 < n:
                    out.append(block[i + 1])
                i += 2
                continue
            if c == in_string:
                in_string = None
            i += 1
            continue
        if c in ("'", '"', "`"):
            in_string = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and block[i + 1] == "/":
            j = block.find("\n", i)
            i = j if j != -1 else n
            continue
        out.append(c)
        i += 1
    return "".join(out)


def find_top_level_entries(content: str) -> dict[str, str]:
    """Split the inner content of an object literal into {key: block} pairs,
    one per top-level `key: { ... }` entry, in source order."""
    content = strip_comments(content)
    entries: dict[str, str] = {}
    pos = 0
    n = len(content)
    entry_re = re.compile(r"\s*(\w+)\s*:\s*\{")
    while pos < n:
        m = entry_re.match(content, pos)
        if not m:
            break
        key = m.group(1)
        open_idx = m.end() - 1
        close_idx = extract_balanced(content, open_idx)
        entries[key] = content[open_idx + 1 : close_idx]
        pos = close_idx + 1
        skip = re.compile(r"\s*,?").match(content, pos)
        pos = skip.end() if skip else pos
    return entries


def extract_exported_const_object(source: str, name: str) -> str:
    m = re.search(rf"export const {re.escape(name)}\s*=\s*\{{", source)
    if not m:
        raise ValueError(f"could not find `export const {name} = {{` in TS source")
    open_idx = m.end() - 1
    close_idx = extract_balanced(source, open_idx)
    return source[open_idx + 1 : close_idx]


_SCALAR_FIELD_RE = re.compile(
    r'(\w+)\s*:\s*(?:"((?:[^"\\]|\\.)*)"|(null)|(-?\d+(?:\.\d+)?)|(true|false))\s*,?'
)


def parse_scalar_fields(block: str) -> dict[str, str | None]:
    """Parse a flat object literal's `key: value` pairs (strings, numbers,
    null, booleans only; this repo's BenchmarkFact objects have no nested
    values). Returns display strings; numbers/bools come back as their
    literal text so callers can compare them uniformly with markdown text."""
    block = strip_comments(block)
    out: dict[str, str | None] = {}
    for m in _SCALAR_FIELD_RE.finditer(block):
        key = m.group(1)
        if m.group(2) is not None:
            out[key] = m.group(2).replace('\\"', '"')
        elif m.group(3) is not None:
            out[key] = None
        elif m.group(4) is not None:
            out[key] = m.group(4)
        elif m.group(5) is not None:
            out[key] = m.group(5)
    return out


@dataclass
class BenchFact:
    key: str
    value: str
    label: str
    scope: str
    status: str
    section: str
    caveat: str | None


@dataclass
class BenchTableRow:
    label: str
    cells: dict[str, str]


@dataclass
class BenchTable:
    key: str
    columns: list[tuple[str, str]]  # (key, header)
    rows: list[BenchTableRow]


def parse_facts_ts(source: str) -> tuple[dict[str, BenchFact], dict[str, BenchTable]]:
    facts_block = extract_exported_const_object(source, "BENCHMARK_FACTS")
    facts: dict[str, BenchFact] = {}
    for key, block in find_top_level_entries(facts_block).items():
        fields = parse_scalar_fields(block)
        facts[key] = BenchFact(
            key=key,
            value=fields.get("value") or "",
            label=fields.get("label") or "",
            scope=fields.get("scope") or "",
            status=fields.get("status") or "",
            section=fields.get("section") or "",
            caveat=fields.get("caveat"),
        )

    tables_block = extract_exported_const_object(source, "BENCHMARK_TABLES")
    tables: dict[str, BenchTable] = {}
    for tkey, tblock in find_top_level_entries(tables_block).items():
        tblock_nc = strip_comments(tblock)

        col_m = re.search(r"columns\s*:\s*\[", tblock_nc)
        columns: list[tuple[str, str]] = []
        if col_m:
            open_idx = col_m.end() - 1
            close_idx = extract_balanced(tblock_nc, open_idx)
            cols_content = tblock_nc[open_idx + 1 : close_idx]
            pos = 0
            entry_re = re.compile(r"\s*\{")
            while True:
                m = entry_re.match(cols_content, pos)
                if not m:
                    break
                oidx = m.end() - 1
                cidx = extract_balanced(cols_content, oidx)
                col_fields = parse_scalar_fields(cols_content[oidx + 1 : cidx])
                columns.append((col_fields.get("key") or "", col_fields.get("header") or ""))
                pos = cidx + 1
                skip = re.compile(r"\s*,?").match(cols_content, pos)
                pos = skip.end() if skip else pos

        rows_m = re.search(r"rows\s*:\s*\[", tblock_nc)
        rows: list[BenchTableRow] = []
        if rows_m:
            open_idx = rows_m.end() - 1
            close_idx = extract_balanced(tblock_nc, open_idx)
            rows_content = tblock_nc[open_idx + 1 : close_idx]
            pos = 0
            entry_re = re.compile(r"\s*\{")
            while True:
                m = entry_re.match(rows_content, pos)
                if not m:
                    break
                oidx = m.end() - 1
                cidx = extract_balanced(rows_content, oidx)
                row_block = rows_content[oidx + 1 : cidx]
                row_fields_top = parse_scalar_fields(re.sub(r"cells\s*:\s*\{[^}]*\}", "", row_block))
                cells_m = re.search(r"cells\s*:\s*\{", row_block)
                cells: dict[str, str] = {}
                if cells_m:
                    c_open = cells_m.end() - 1
                    c_close = extract_balanced(row_block, c_open)
                    cell_fields = parse_scalar_fields(row_block[c_open + 1 : c_close])
                    cells = {k: (v if v is not None else "") for k, v in cell_fields.items()}
                rows.append(BenchTableRow(label=row_fields_top.get("label") or "", cells=cells))
                pos = cidx + 1
                skip = re.compile(r"\s*,?").match(rows_content, pos)
                pos = skip.end() if skip else pos

        tables[tkey] = BenchTable(key=tkey, columns=columns, rows=rows)

    return facts, tables


# ─── Markdown scanning ───────────────────────────────────────────────────────


def github_slug(text: str) -> str:
    """GitHub's heading-anchor algorithm: strip inline code/formatting marks,
    lowercase, drop anything that isn't a letter/digit/space/hyphen, then
    turn spaces into hyphens."""
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = text.lower()
    text = re.sub(r"[^a-z0-9 \-]", "", text)
    text = re.sub(r"\s+", "-", text.strip())
    return text


@dataclass
class Heading:
    level: int
    text: str
    anchor: str
    start: int  # char offset of the section body (right after the heading line)


def parse_headings(md: str) -> list[Heading]:
    headings: list[Heading] = []
    seen: dict[str, int] = {}
    for m in re.finditer(r"^(#{1,6})\s+(.+?)\s*$", md, re.MULTILINE):
        level = len(m.group(1))
        text = m.group(2)
        anchor = github_slug(text)
        if anchor in seen:
            seen[anchor] += 1
            anchor = f"{anchor}-{seen[anchor]}"
        else:
            seen[anchor] = 0
        headings.append(Heading(level=level, text=text, anchor=anchor, start=m.end()))
    return headings


def section_text(md: str, headings: list[Heading], anchor: str) -> str | None:
    anchor = anchor.lstrip("#")
    for i, h in enumerate(headings):
        if h.anchor != anchor:
            continue
        end = len(md)
        for later in headings[i + 1 :]:
            if later.level <= h.level:
                end = later.start - len(later.text) - later.level - 4
                # crude but fine: fall back to searching for the next heading
                # match start rather than computing exact offsets
                break
        # Recompute end precisely by finding the next heading of equal-or-
        # shallower level starting strictly after this heading.
        end = len(md)
        for later in headings[i + 1 :]:
            if later.level <= h.level:
                # later.start is right after that heading line; back up to
                # the start of that heading line.
                line_start = md.rfind("\n", 0, later.start)
                end = line_start if line_start != -1 else later.start
                break
        return md[h.start : end]
    return None


_MD_STRIP_RE = re.compile(r"\*\*([^*]*)\*\*|__([^_]*)__|`([^`]*)`|\*([^*]*)\*")


def strip_md_emphasis(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        return next(g for g in m.groups() if g is not None)

    prev = None
    while prev != text:
        prev = text
        text = _MD_STRIP_RE.sub(repl, text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class MdTable:
    header: list[str]
    rows: list[list[str]]
    start: int


def parse_markdown_tables(md: str) -> list[MdTable]:
    lines = md.split("\n")
    tables: list[MdTable] = []
    offsets = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1

    i = 0
    while i < len(lines) - 1:
        line = lines[i].strip()
        sep = lines[i + 1].strip()
        if line.startswith("|") and re.match(r"^\|?[\s:|-]+\|?$", sep) and "-" in sep:
            header = [strip_md_emphasis(c) for c in _split_row(line)]
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([strip_md_emphasis(c) for c in _split_row(lines[j].strip())])
                j += 1
            tables.append(MdTable(header=header, rows=rows, start=offsets[i]))
            i = j
        else:
            i += 1
    return tables


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


# ─── Comparison ──────────────────────────────────────────────────────────────


@dataclass
class Report:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unverifiable: list[str] = field(default_factory=list)
    tables_checked: int = 0
    facts_checked: int = 0

    def fail(self, msg: str) -> None:
        self.failures.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


TABLE_SECTION_ANCHORS = {
    "retrieval_sealed": "#1-finding-the-right-files",
    "agent_loop_codex": "#the-main-run-48-questions-on-codex-gpt-56-sol",
    "agent_loop_claude": "#the-second-proof-point-15-questions-on-claude-code-claude-sonnet-5",
    "context_load": "#3-loading-one-commits-context-the-easy-number",
    "health_vs_codescene": "#5-code-health-predicts-defects",
    "indexing_time": "#6-indexing-time-the-row-we-lose",
}
# The mapping above is hand-maintained rather than inferred, because matching
# a TS table to "the" markdown table under a heading generically (when a
# section can hold more than one table) is exactly the kind of general
# markdown parser this script is deliberately avoiding. If BENCHMARKS.md's
# section structure changes, update this map; the script will otherwise
# report the table as unmatched rather than silently comparing the wrong one.


def find_md_table_for(md: str, headings: list[Heading], anchor: str) -> MdTable | None:
    text = section_text(md, headings, anchor)
    if text is None:
        return None
    tables = parse_markdown_tables(text)
    return tables[0] if tables else None


NUM_TOKEN_RE = re.compile(r"-?\d[\d,]*\.?\d*%?")


def check_tables(ts_tables: dict[str, BenchTable], md: str, headings: list[Heading], report: Report) -> None:
    for tkey, table in ts_tables.items():
        anchor = TABLE_SECTION_ANCHORS.get(tkey)
        if anchor is None:
            report.warn(
                f"table '{tkey}': no known markdown section mapping in this script; add one to "
                "TABLE_SECTION_ANCHORS if this table is new"
            )
            continue
        md_table = find_md_table_for(md, headings, anchor)
        if md_table is None:
            report.fail(f"table '{tkey}': could not find a markdown table under {anchor} in docs/BENCHMARKS.md")
            continue

        report.tables_checked += 1
        md_header_lc = [h.lower() for h in md_table.header]

        col_index: dict[str, int] = {}
        for i, (ckey, header) in enumerate(table.columns):
            if header.lower() in md_header_lc:
                col_index[ckey] = md_header_lc.index(header.lower())
            elif i < len(md_table.header):
                col_index[ckey] = i  # positional fallback (e.g. blank markdown header cells)

        if not table.columns or table.columns[0][0] not in col_index:
            report.fail(f"table '{tkey}': could not locate its label column in the matched markdown table")
            continue
        label_key = table.columns[0][0]
        label_idx = col_index[label_key]
        md_labels = [strip_md_emphasis(r[label_idx]) if label_idx < len(r) else "" for r in md_table.rows]

        for row in table.rows:
            ts_label = row.cells.get(label_key, row.label)
            if ts_label not in md_labels:
                report.fail(
                    f"table '{tkey}': row '{ts_label}' not found in the markdown table under {anchor} "
                    f"(markdown rows: {md_labels})"
                )
                continue
            md_row = md_table.rows[md_labels.index(ts_label)]
            for ckey, header in table.columns:
                if ckey not in col_index:
                    continue
                idx = col_index[ckey]
                if idx >= len(md_row):
                    continue
                ts_val = row.cells.get(ckey, "")
                md_val = md_row[idx]
                # The markdown table sometimes repeats the column name inside
                # the cell itself (a "p" column holding "p = 0.003") where the
                # frontend just holds "0.003" under a "p" header. Strip that
                # redundant prefix before comparing rather than treating it
                # as drift.
                if header.strip().lower() == "p":
                    md_val_cmp = re.sub(r"^p\s*=\s*", "", md_val, flags=re.IGNORECASE)
                else:
                    md_val_cmp = md_val
                if ts_val == md_val_cmp:
                    continue
                if not any(c.isdigit() for c in ts_val) and not any(c.isdigit() for c in md_val_cmp):
                    # Free-text descriptive cells (e.g. "what it builds") are
                    # expected to be worded differently on the marketing site
                    # than in the doc's table; only numeric cells are asserted
                    # exactly. Divergence here is reported, not failed.
                    report.warn(
                        f"table '{tkey}', row '{ts_label}', column '{header}': descriptive text differs "
                        f"(frontend {ts_val!r} vs docs {md_val!r}), not a numeric cell, not treated as drift"
                    )
                    continue
                report.fail(
                    f"table '{tkey}', row '{ts_label}', column '{header}': "
                    f"frontend has {ts_val!r}, docs/BENCHMARKS.md has {md_val!r}"
                )


def check_facts(facts: dict[str, BenchFact], md: str, headings: list[Heading], report: Report) -> None:
    anchors = {f"#{h.anchor}" for h in headings}
    for key, fact in facts.items():
        report.facts_checked += 1

        if fact.status == "provisional":
            report.warn(f"fact '{key}' is status=provisional ({fact.label!r}), a run in flight, do not forget it")

        if fact.status == "published" and not fact.caveat:
            report.fail(f"fact '{key}' is status=published but has no caveat")

        if fact.section not in anchors:
            report.fail(
                f"fact '{key}' points at section '{fact.section}', which is not a heading in docs/BENCHMARKS.md"
            )
            continue

        text = section_text(md, headings, fact.section) or ""
        if fact.value and fact.value in text:
            continue

        tokens = NUM_TOKEN_RE.findall(fact.value)
        if tokens and all(tok in text for tok in tokens):
            report.warn(
                f"fact '{key}': value {fact.value!r} not found verbatim in section {fact.section}, "
                f"but its numeric tokens {tokens} all appear there (likely paraphrased prose)"
            )
        elif tokens:
            report.fail(
                f"fact '{key}': value {fact.value!r}, numeric token(s) {tokens} missing from "
                f"section {fact.section} in docs/BENCHMARKS.md"
            )
        else:
            report.unverifiable.append(
                f"fact '{key}': value {fact.value!r} has no numeric content and does not appear verbatim "
                f"in section {fact.section}; this script cannot verify prose-only values"
            )


# ─── Entry point ─────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    repo_root = Path(__file__).resolve().parent.parent
    parser.add_argument(
        "--benchmarks",
        type=Path,
        default=repo_root / "docs" / "BENCHMARKS.md",
        help="Path to docs/BENCHMARKS.md",
    )
    parser.add_argument(
        "--facts-ts",
        type=Path,
        default=None,
        help="Path to frontend/src/lib/benchmark-facts.ts. Defaults to frontend/src/lib/benchmark-facts.ts "
        "next to this repo; if that default is absent, the check is skipped (exit 0), since frontend/ is a "
        "separate repo not checked into this one.",
    )
    args = parser.parse_args()

    facts_ts_path = args.facts_ts
    used_default = facts_ts_path is None
    if used_default:
        facts_ts_path = repo_root / "frontend" / "src" / "lib" / "benchmark-facts.ts"

    if not facts_ts_path.exists():
        if used_default:
            print(
                f"[check_benchmark_facts] {facts_ts_path} not found; frontend/ is a separate repo not "
                "checked into this one, so this is expected in CI. Skipping."
            )
            return 0
        print(f"[check_benchmark_facts] error: {facts_ts_path} does not exist", file=sys.stderr)
        return 1

    if not args.benchmarks.exists():
        print(f"[check_benchmark_facts] error: {args.benchmarks} does not exist", file=sys.stderr)
        return 1

    md = args.benchmarks.read_text(encoding="utf-8")
    ts_source = facts_ts_path.read_text(encoding="utf-8")

    headings = parse_headings(md)

    try:
        facts, tables = parse_facts_ts(ts_source)
    except ValueError as exc:
        print(f"[check_benchmark_facts] error parsing {facts_ts_path}: {exc}", file=sys.stderr)
        return 1

    report = Report()
    check_tables(tables, md, headings, report)
    check_facts(facts, md, headings, report)

    for w in report.warnings:
        print(f"WARN: {w}")
    for u in report.unverifiable:
        print(f"UNVERIFIABLE: {u}")
    for f in report.failures:
        print(f"FAIL: {f}")

    print(
        f"\nchecked {report.facts_checked} facts, {report.tables_checked} tables; "
        f"{len(report.unverifiable)} facts unverifiable (prose-only value, no numeric content); "
        f"{len(report.warnings)} warning(s); {len(report.failures)} failure(s)"
    )

    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
