# duplication/

Native Rabin-Karp clone detection over tree-sitter tokens. No `jscpd`,
no Node.js, no extra Python runtime deps.

## Public API

```python
from repowise.core.analysis.health.duplication import detect_clones

report = detect_clones(parsed_files, git_meta_map)
# report.pairs                : list[ClonePair]
# report.duplication_pct      : dict[file_path -> percent duplicated]
# report.pairs_by_file        : dict[file_path -> list[ClonePair]]
```

## Pipeline

1. **Tokenize** — `tokenizer.tokenize_file(language, source_bytes)` walks
   the tree-sitter AST, drops whitespace and comments, and yields a
   normalized token stream (identifiers collapse to `ID`, literals to
   `LIT`, operators/keywords pass through). Renaming a variable does
   not break a match; restructuring control flow does.
2. **Hash windows** — `rabin_karp.rolling_hashes` produces a 64-bit
   polynomial hash for every sliding window of `window_tokens` (default
   50). Constants are pinned so hashes are reproducible across
   processes.
3. **Bucket + verify** — equal hashes inside a bucket are candidate
   matches. `detector._tokens_equal` walks the two token kind sequences
   to rule out hash collisions before emitting a `ClonePair`.
4. **Merge adjacent windows** — adjacent or overlapping clone windows
   between the same `(file_a, file_b)` collapse into one contiguous
   region.
5. **Co-change correlation** — for each surviving pair we look up
   `git_meta_map[file_a]['co_change_partners_json']` (and the reverse
   direction). The max count attaches to `ClonePair.co_change_count`
   so the `dry_violation` biomarker can rank *active* duplicates
   higher than dormant ones — the Phase-3 hard constraint.

## Inputs

- `parsed_files`: `list[ParsedFile]` from the ingestion phase. The
  detector reads source from each file's `abs_path`.
- `git_meta_map`: optional per-file metadata. When present, the
  detector parses `co_change_partners_json` to weight clone pairs.

## Outputs

- `DuplicationReport`:
  - `pairs`: every verified, merged clone region.
  - `duplication_pct`: per-file duplicate-line percentage — the union
    of clone-pair line ranges over file NLOC, so overlapping pairs
    don't double-count the same physical lines.
  - `pairs_by_file`: lookup map used by the `dry_violation` biomarker.

## Extension points

- **Tunables** — `DEFAULT_WINDOW_TOKENS` (sensitivity) and
  `DEFAULT_MIN_LINES` (output filter) are arguments to `detect_clones`.
  Future `HealthConfig` knobs can be threaded through here without
  touching biomarker code.
- **Tokenizer** — to support a new language with different identifier
  / literal node types, extend the kind sets in `tokenizer.py`. The
  rolling hash is language-agnostic.

## Performance notes

- Hashing is `O(total_tokens)`; bucket walk is `O(sum_of_collisions)`.
  For repos with low duplication the bucket walk is near-linear.
- The tokenizer re-parses every file independently of the complexity
  walker because the ingestion `ParsedFile` does not retain a
  tree-sitter `Tree`. Tradeoff is documented; switching to a shared
  parse cache is a Phase 5 optimization.
