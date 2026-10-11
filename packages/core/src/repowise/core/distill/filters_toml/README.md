# Data-driven output filters

Each `*.toml` in this directory defines one command filter as pure data. A
single generic applier (`repowise.core.distill.toml_filter.TomlFilter`)
interprets every definition, so adding coverage for a new command family is a
data file plus inline tests, not a Python module and a round of router/config/
doctor edits.

Files here load once, after the hand-written Python filters, via
`FilterRegistry._ensure_loaded()`. Ordering is stable (sorted by filename) so
content-sniff tie-breaks are deterministic.

## The errors-first invariant is enforced by the engine

No field can drop a line that `is_error_line` classifies as a failure. The
applier re-keeps any error line a `strip`/`keep`/cap would have removed, skips
`replace`/`truncate` on error lines, and only fires a `match_output`
short-circuit when the whole output is error-free. A filter author cannot
author an error line away.

## Fields

Top-level tables:

| Table | Purpose |
|-------|---------|
| `[meta]` | Test metadata. `savings_floor` is the CI median-savings floor over this file's inline cases (default 60). |
| `[filters.<name>]` | One filter definition. `<name>` is the registry/ledger name and must match the `rewrite_hook.py` family entry. |
| `[[tests.<name>]]` | Inline cases (`name`, `input`, `expected`) run in CI by `test_toml_filters.py`. |

Filter fields (all optional except a `match_*`):

| Field | Type | Effect |
|-------|------|--------|
| `description` | str | Human summary. |
| `priority` | int | Lower runs first when several filters match (default 50). |
| `min_lines` | int | Outputs shorter than this are never distilled (default 8). |
| `match_command` | regex | Route when the normalized command matches. |
| `match_content` | regex | Route by content when the command is unknown (surfaces that see only bytes). |
| `strip_ansi` | bool | Strip ANSI escapes before filtering. |
| `strip_lines_matching` | list[regex] | Drop matching lines (block-list mode). |
| `keep_lines_matching` | list[regex] | Keep only matching lines (allow-list mode; wins over `strip`). |
| `replace` | list[{pattern, replacement}] | Regex substitutions on surviving non-error lines. |
| `match_output` | list[{pattern, message}] | Short-circuit to `message` when `pattern` is present and the output is error-free. |
| `truncate_lines_at` | int | Truncate long non-error lines to this width with an ellipsis. |
| `max_lines` / `tail_lines` | int | Cap head / tail line counts (error lines in the middle are retained). |
| `on_empty` | str | Rendering when nothing survives (e.g. `install: ok (no changes)`). |

`keep_lines_matching` and `strip_lines_matching` are mutually exclusive per
filter: if `keep` is set it takes precedence and `strip` is ignored.

## Adding a filter

1. Write `<family>.toml` with a `match_command` (and usually a `match_content`)
   plus inline `[[tests.<family>]]` cases and a `[meta] savings_floor`.
2. Add a plain-regex row for the family to `FAMILY_PATTERNS` in
   `packages/cli/src/repowise/cli/rewrite_hook.py` so live commands get
   rewritten (that table is a stdlib-only hot-path mirror; no core import).
3. Add a routing row to `test_toml_filters.py::test_routing_no_regression`.

If a filter needs behavior no field covers, add the field to the applier once
(not per-filter) and document it in the table above.
