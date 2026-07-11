# core/sessions: the shared agent-transcript layer

One normalized event stream over coding-agent session transcripts, so every
consumer (savings mining, correction mining, session decision mining) reads
the same `Event` objects instead of parsing harness-specific JSONL itself.

Everything here is read-only and local: transcripts are read from the user's
own machine and never leave it.

## Layout

| File | What it holds |
|------|---------------|
| `events.py` | The `Event` / `ToolUse` / `ToolResult` model, the `INTERRUPT_MARKER` constant, and `iter_deduped_usage` |
| `adapters/base.py` | The `HarnessAdapter` contract: `discover()` + `normalize()`, with shared `iter_events()` built on top |
| `adapters/claude_code.py` | First adapter: `~/.claude/projects/<munged-cwd>/*.jsonl` |
| `cursor.py` | `CursorStore` (byte offset + mtime per file) and `iter_new_events` for incremental scans |
| `staging.py` | `SessionStagingStore`: the `.repowise/sessions/sessions.db` sidecar (WAL) holding mined candidates, observation counts, and DB-backed cursors |
| `miners/decisions.py` | Session-sourced decisions: deterministic gates over Events, one batched LLM structuring pass per update, observation-counted promotion |

## The contract

An adapter implements exactly two things:

- `discover(repo_root, projects_root=None)`: transcript files for sessions
  rooted at a repo. Absent directory means empty list, never an error.
- `normalize(raw_line)`: one raw transcript line to an `Event`, or `None`
  when the line is unparseable. Never raises on content.

Iteration, prefiltering, and cursoring are shared code. Consumers that only
care about a slice of the stream pass a `prefilter` callable that gates on
the raw string before any JSON parsing happens; transcript lines routinely
run to hundreds of kilobytes, so this is the difference between skimming and
parsing whole sessions.

## Claude Code schema gotchas (encoded in the adapter and fixtures)

- One API message spans several JSONL lines (one per content block) and each
  repeats the full `usage` object. Summing raw lines overcounts roughly
  2.6x; always go through `iter_deduped_usage`, which keys on `message.id`.
- `isSidechain` (subagent) lines are kept in the stream and flagged, not
  dropped. Subagent activity is real activity; filter it out per consumer
  if you must.
- `isMeta` and `isCompactSummary` lines are flagged: their text is
  harness-injected or synthetic, so skip them for prompt-derived signals.
- A user interrupt is the text marker `[Request interrupted by user`;
  `Event.interrupted` checks it.
- A shell tool result's top-level `toolUseResult` is a dict on success and
  an `Error: Exit code N` string on command failure; it rides on
  `ToolResult.payload`.

## Cursors

Transcripts are append-only, so a byte offset at a line boundary is a full
resume point. `iter_new_events(adapter, path, store)` seeks to the stored
offset, yields only appended events, and advances the in-memory cursor per
consumed line; the caller batches one `store.save()` per scan. A truncated
or replaced file restarts from zero; a trailing line without a newline is a
write in progress and waits for the next pass.

## Adding a harness

Subclass `HarnessAdapter` in `adapters/<name>.py`, populate whatever fields
the harness records (leave the rest at defaults), and add a real-shaped
JSONL fixture under `tests/unit/sessions/data/`. Consumers written against
`Event` need no changes.
