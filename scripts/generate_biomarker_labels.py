"""Generate the web glossary's biomarker labels from core's one copy.

Core's ``repowise.core.analysis.health.biomarker_labels`` is the source; the web
glossary reads ``packages/ui/src/health/generated/biomarker-labels.ts``. Run
``python scripts/generate_biomarker_labels.py`` to refresh, ``--check`` to fail
when the checked-in file is stale.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

_OUT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "packages/ui/src/health/generated/biomarker-labels.ts"
)

_HEADER = """// Generated from repowise.core.analysis.health.biomarker_labels. Do not edit.
//
// Regenerate with:  python scripts/generate_biomarker_labels.py
// tests/unit/health/test_generated_biomarker_labels.py fails when they disagree.
"""


def render(labels: dict[str, str]) -> str:
    rows = "".join(f"  {name}: {json.dumps(label, ensure_ascii=False)},\n" for name, label in labels.items())
    return f"{_HEADER}\nexport const BIOMARKER_LABELS = {{\n{rows}}} as const;\n"


def _labels() -> dict[str, str]:
    from repowise.core.analysis.health.biomarker_labels import BIOMARKER_LABELS

    return BIOMARKER_LABELS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the file is stale.")
    args = parser.parse_args(argv)

    rendered = render(_labels())

    if args.check:
        current = _OUT.read_text(encoding="utf-8") if _OUT.exists() else ""
        if current != rendered:
            print(
                f"{_OUT.relative_to(_OUT.parents[5])} is stale.\n"
                "Run: python scripts/generate_biomarker_labels.py",
                file=sys.stderr,
            )
            return 1
        return 0

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    # LF on every platform, as .gitattributes commits it.
    with _OUT.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(rendered)
    print(f"wrote {_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
