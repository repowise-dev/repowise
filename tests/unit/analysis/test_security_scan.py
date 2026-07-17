"""Security scanner: real-source scanning and idempotent persistence.

Regression anchor: ``persist_ingestion`` used to feed the scanner
``getattr(pf.file_info, "content", "")``, but ``FileInfo`` has no ``content``
attribute, so every line-pattern scan ran against an empty string and could
never produce a finding. The wiring now reads ``result.source_map``. Rows are
also replaced per scanned file instead of appended, so repeated indexing no
longer accumulates duplicates.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text

from repowise.core.analysis.security_scan import SecurityScanner

SNIPPY = b"""import pickle

password = 'hunter2'
data = pickle.loads(blob)
x = eval(user_input)
"""

CLEAN = b"""def add(a, b):
    return a + b
"""


def _fake_result(files: dict[str, bytes]):
    parsed = [
        SimpleNamespace(file_info=SimpleNamespace(path=p), symbols=[])
        for p in files
    ]
    return SimpleNamespace(parsed_files=parsed, source_map=dict(files))


async def _fresh_session_factory(tmp_path: Path):
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
    )
    from repowise.core.persistence.models import Repository

    (tmp_path / ".repowise").mkdir(exist_ok=True)
    engine = create_engine(get_db_url_for_repo(tmp_path))
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        session.add(Repository(id="repo-1", name="t", local_path=str(tmp_path)))
    return engine, sf


async def _rows(sf) -> list[tuple]:
    from repowise.core.persistence import get_session

    async with get_session(sf) as session:
        res = await session.execute(
            text(
                "SELECT file_path, kind, line_number FROM security_findings "
                "ORDER BY file_path, line_number, kind"
            )
        )
        return [tuple(r) for r in res.fetchall()]


class TestScanFile:
    def test_line_patterns_fire_on_real_source(self) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(
            scanner.scan_file("a.py", SNIPPY.decode(), symbols=[])
        )
        kinds = {f["kind"] for f in findings}
        assert "hardcoded_password" in kinds
        assert "pickle_loads" in kinds
        assert "eval_call" in kinds
        by_kind = {f["kind"]: f for f in findings}
        assert by_kind["hardcoded_password"]["line"] == 3

    def test_clean_source_yields_nothing(self) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("b.py", CLEAN.decode(), symbols=[]))
        assert findings == []


class TestPersistSecurityFindings:
    """The persist.py wiring: source_map in, idempotent rows out."""

    def test_findings_fire_from_source_map(self, tmp_path: Path) -> None:
        from repowise.core.pipeline.persist import persist_security_findings

        async def _run():
            engine, sf = await _fresh_session_factory(tmp_path)
            from repowise.core.persistence import get_session

            result = _fake_result({"a.py": SNIPPY, "clean.py": CLEAN})
            async with get_session(sf) as session:
                await persist_security_findings(result, session, "repo-1")
            rows = await _rows(sf)
            await engine.dispose()
            return rows

        rows = asyncio.run(_run())
        assert rows, "line-pattern findings must land from source_map bytes"
        assert all(r[0] == "a.py" for r in rows)

    def test_rescan_does_not_accumulate_duplicates(self, tmp_path: Path) -> None:
        from repowise.core.pipeline.persist import persist_security_findings

        async def _run():
            engine, sf = await _fresh_session_factory(tmp_path)
            from repowise.core.persistence import get_session

            result = _fake_result({"a.py": SNIPPY})
            for _ in range(3):
                async with get_session(sf) as session:
                    await persist_security_findings(result, session, "repo-1")
            rows = await _rows(sf)
            await engine.dispose()
            return rows

        rows = asyncio.run(_run())
        counts = {}
        for r in rows:
            counts[r] = counts.get(r, 0) + 1
        assert all(c == 1 for c in counts.values()), f"duplicated rows: {counts}"

    def test_cleaned_file_loses_its_rows(self, tmp_path: Path) -> None:
        from repowise.core.pipeline.persist import persist_security_findings

        async def _run():
            engine, sf = await _fresh_session_factory(tmp_path)
            from repowise.core.persistence import get_session

            async with get_session(sf) as session:
                await persist_security_findings(
                    _fake_result({"a.py": SNIPPY}), session, "repo-1"
                )
            async with get_session(sf) as session:
                await persist_security_findings(
                    _fake_result({"a.py": CLEAN}), session, "repo-1"
                )
            rows = await _rows(sf)
            await engine.dispose()
            return rows

        assert asyncio.run(_run()) == []

    def test_missing_source_map_degrades_to_symbol_scan(self, tmp_path: Path) -> None:
        """A result without source_map (resume views) must not crash and must
        still run the symbol-name scan."""
        from repowise.core.pipeline.persist import persist_security_findings

        async def _run():
            engine, sf = await _fresh_session_factory(tmp_path)
            from repowise.core.persistence import get_session

            # NOTE: _SYMBOL_KEYWORDS uses \b boundaries and underscores are
            # word chars, so the keyword must stand alone in the name.
            sym = SimpleNamespace(name="auth", start_line=7)
            result = SimpleNamespace(
                parsed_files=[
                    SimpleNamespace(
                        file_info=SimpleNamespace(path="auth.py"), symbols=[sym]
                    )
                ],
            )
            async with get_session(sf) as session:
                await persist_security_findings(result, session, "repo-1")
            rows = await _rows(sf)
            await engine.dispose()
            return rows

        rows = asyncio.run(_run())
        assert ("auth.py", "security_sensitive_symbol", 7) in rows
