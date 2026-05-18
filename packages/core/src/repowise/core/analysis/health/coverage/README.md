# coverage/

Phase 2. Coverage report ingestion + test-file heuristic.

Placeholder in Phase 1 — module exists so the layout matches the plan
doc but no parsers ship yet. Filled in by Phase 2.

## Planned files

- `model.py` — `CoverageReport`, `FileCoverage` dataclasses.
- `detector.py` — format auto-detection (extension + magic-byte sniff)
  and test-file heuristic.
- `lcov.py` — LCOV parser (stdlib only).
- `cobertura.py` — Cobertura XML parser (stdlib `xml.etree`).
- `clover.py` — Clover XML parser.
