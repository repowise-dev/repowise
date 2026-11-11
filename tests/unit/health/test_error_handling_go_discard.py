"""Go ``go_swallow``: discards the call's contract makes deliberate are not findings.

``strconv`` parsers return the zero value on failure, which the caller takes as
its default; ``_, _ = w.Write(b)`` blanks every result of a write on purpose.
Other discarded errors, and a write whose byte count is kept, stay findings.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file


def _kinds(body: str) -> list[str]:
    src = f"package p\n\nfunc f() {{\n\t{body}\n}}\n".encode()
    return [h.kind for h in walk_file("<fixture>", "go", src).error_handling_hits]


@pytest.mark.parametrize(
    "body",
    [
        "n, _ := strconv.Atoi(s)",
        "field.Scale, _ = strconv.Atoi(s)",
        "r, _ := strconv.ParseInt(hex[1:3], 16, 0)",
        "if isUTC, _ := strconv.ParseBool(tag); isUTC {\n\t\tl = time.UTC\n\t}",
        "_, _ = w.Write(b)",
        '_, _ = builder.WriteString(", ")',
        "_, _ = self.guiIO.newCmdWriterFn().Write(out.Bytes())",
        '_, _ = fmt.Fprintf(w, "%s\\n", name)',
        "_, _ = io.WriteString(w, s)",
    ],
)
def test_deliberate_discard_is_not_swallow(body: str) -> None:
    assert _kinds(body) == []


@pytest.mark.parametrize(
    "body",
    [
        "v, _ := os.ReadFile(p)",
        "n, _ := conn.Write(b)",
        "_, _ = io.Copy(dst, src)",
        "_, _ = db.Exec(q)",
        "n, _ := strings.NewReader(s).Read(buf)",
    ],
)
def test_other_discards_stay_findings(body: str) -> None:
    assert _kinds(body) == ["go_swallow"]
