"""The health pass reads a clone partner's lines by repo path, once."""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.health import HealthAnalyzer


def test_a_partner_outside_the_parsed_set_is_read_under_the_repo_root(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    reads: list[str] = []

    def read_source(path: str) -> bytes | None:
        reads.append(path)
        return Path(path).read_bytes() if Path(path).is_file() else None

    # An incremental pass may hand over only the files it re-parsed.
    analyzer = HealthAnalyzer(None, parsed_files=[], repo_root=tmp_path, source_reader=read_source)
    assert analyzer._repo_lines("pkg/a.py") == ["x = 1", "y = 2"]
    assert analyzer._repo_lines("pkg/a.py") == ["x = 1", "y = 2"]
    assert len(reads) == 1
    assert analyzer._repo_lines("pkg/missing.py") is None
