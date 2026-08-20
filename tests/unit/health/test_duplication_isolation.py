"""Large duplication scans release their transient allocator arenas."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from repowise.core.analysis.health.duplication import detect_clones
from repowise.core.analysis.health.duplication.isolation import (
    detect_clones_with_isolation,
)


def _parsed(path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        file_info=SimpleNamespace(
            path=path.name,
            abs_path=str(path),
            language="python",
        )
    )


def _key(report):
    return sorted(
        (
            pair.file_a,
            pair.file_b,
            pair.a_start_line,
            pair.a_end_line,
            pair.b_start_line,
            pair.b_end_line,
        )
        for pair in report.pairs
    )


def test_isolated_detection_matches_in_process(tmp_path: Path):
    body = "\n".join(f"value_{index} = source_{index} + 1" for index in range(30))
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_text(body)
    second.write_text(body)
    parsed = [_parsed(first), _parsed(second)]

    expected = detect_clones(parsed, window_tokens=20, min_lines=4)
    actual = detect_clones_with_isolation(
        parsed,
        window_tokens=20,
        min_lines=4,
        cache_dir=tmp_path / ".repowise",
        isolation_file_threshold=0,
    )

    assert _key(actual) == _key(expected)
    assert actual.duplication_pct == expected.duplication_pct
    assert (tmp_path / ".repowise" / "duplication_cache.pkl").exists()
