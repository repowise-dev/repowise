# duplication/

Phase 3. Native Rabin–Karp over tree-sitter tokens. No `jscpd`, no
Node.js dependency.

Placeholder in Phase 1 — module exists so the layout matches the plan
doc but no detectors ship yet. Filled in by Phase 3.

## Planned files

- `tokenizer.py` — tree-sitter token extraction (whitespace / comments
  excluded).
- `rabin_karp.py` — rolling hash over token windows.
- `detector.py` — clone-pair construction + co-change correlation.
