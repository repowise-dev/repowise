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

import pytest
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
    parsed = [SimpleNamespace(file_info=SimpleNamespace(path=p), symbols=[]) for p in files]
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
        findings = asyncio.run(scanner.scan_file("a.py", SNIPPY.decode(), symbols=[]))
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

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("eval(payload)\n", {"eval_call"}),
            ("exec (payload)\n", {"exec_call"}),
            ("eval(\n    payload\n)\n", {"eval_call"}),
            ("builtins.eval(payload)\n", {"eval_call"}),
            ("retrieval (payload)\n", set()),
            ("my_eval(payload)\n", set()),
            ("# eval(payload)\n", set()),
            ('text = "eval(payload)"\n', set()),
            ('"""exec(payload)"""\n', set()),
        ],
    )
    def test_python_eval_exec_call_corpus(self, source: str, expected: set[str]) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("calls.py", source, symbols=[]))
        assert {row["kind"] for row in findings if row["kind"].endswith("_call")} == expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("eval (payload);\n", {"eval_call"}),
            ("window.eval(payload);\n", {"eval_call"}),
            ("const result = `${eval(payload)}`;\n", {"eval_call"}),
            ("retrieval (payload);\n", set()),
            ("my_eval(payload);\n", set()),
            ("// eval(payload)\n", set()),
            ("/* exec(payload) */\n", set()),
            ('const text = "eval(payload)";\n', set()),
            # ``exec`` is not a global in JavaScript, so a bare call is a local
            # function, and a call on a receiver is ``RegExp.prototype.exec``.
            # Neither spawns anything; see the corpus test below.
            ("exec\n  (payload);\n", set()),
            ("const m = /^a(b)$/.exec(cell);\n", set()),
            ("while ((m = re.exec(expr)) !== null) {}\n", set()),
        ],
    )
    def test_fallback_eval_exec_call_corpus(self, source: str, expected: set[str]) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("calls.js", source, symbols=[]))
        assert {row["kind"] for row in findings if row["kind"].endswith("_call")} == expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ('import { exec } from "child_process";\nexec(cmd);\n', {"exec_call"}),
            ('const cp = require("child_process");\ncp.execSync(cmd);\n', {"exec_call"}),
            (
                'import { execFile } from "node:child_process";\nexecFile(bin, args);\n',
                {"exec_call"},
            ),
            # Regex parsing in a file that also spawns: the gate is per file, so
            # this is the residual false positive the gate cannot remove.
            ('require("child_process");\nconst m = /a/.exec(s);\n', {"exec_call"}),
        ],
    )
    def test_js_exec_is_reported_only_when_child_process_is_present(
        self, source: str, expected: set[str]
    ) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("spawn.ts", source, symbols=[]))
        assert {row["kind"] for row in findings if row["kind"].endswith("_call")} == expected

    def test_combined_prefilter_uses_the_same_safe_call_boundaries(self) -> None:
        from repowise.core.analysis.security_scan import _ANY_PATTERN

        assert _ANY_PATTERN.search("eval (")
        assert _ANY_PATTERN.search("exec(")
        assert _ANY_PATTERN.search("window.eval(")
        assert not _ANY_PATTERN.search("retrieval (")
        assert not _ANY_PATTERN.search("my_eval(")

    @pytest.mark.parametrize(
        "source",
        [
            'const API_KEY = "abc123";\n',
            "const N8N_API_KEY = 'abc123';\n",
            'SECRET = "abc123"\n',
            'PASSWORD = "abc123"\n',
        ],
    )
    def test_secret_patterns_are_case_insensitive(self, source: str) -> None:
        """The constant form is how a pinned credential is usually written.

        Also covers the prefilter: ``_ANY_PATTERN`` is built from the patterns'
        source text, so a per-pattern ``re.IGNORECASE`` flag would be dropped
        there and the line would never reach the pattern loop.
        """
        from repowise.core.analysis.security_scan import _ANY_PATTERN

        assert _ANY_PATTERN.search(source)
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("config.ts", source, symbols=[]))
        assert {row["kind"] for row in findings} & {"hardcoded_secret", "hardcoded_password"}

    def test_single_line_subprocess_shell_true_is_flagged(self) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        source = 'subprocess.run("rm -rf " + path, shell=True)\n'
        findings = asyncio.run(scanner.scan_file("a.py", source, symbols=[]))
        hits = [f for f in findings if f["kind"] == "subprocess_shell_true"]
        assert len(hits) == 1
        assert hits[0]["line"] == 1

    def test_multiline_subprocess_shell_true_is_flagged(self) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        source = 'subprocess.run(\n    "rm -rf " + path,\n    shell=True,\n)\n'
        findings = asyncio.run(scanner.scan_file("a.py", source, symbols=[]))
        hits = [f for f in findings if f["kind"] == "subprocess_shell_true"]
        assert len(hits) == 1
        assert hits[0]["line"] == 1

    def test_multiline_popen_shell_true_is_flagged(self) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        source = 'subprocess.Popen(\n    ["/bin/sh"],\n    shell=True,\n)\n'
        findings = asyncio.run(scanner.scan_file("a.py", source, symbols=[]))
        hits = [f for f in findings if f["kind"] == "subprocess_shell_true"]
        assert len(hits) == 1
        assert hits[0]["line"] == 1

    def test_subprocess_without_shell_does_not_fire(self) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        source = 'subprocess.run(\n    ["git", "rev-parse", "HEAD"],\n    capture_output=True,\n)\n'
        findings = asyncio.run(scanner.scan_file("a.py", source, symbols=[]))
        hits = [f for f in findings if f["kind"] == "subprocess_shell_true"]
        assert hits == []

    def test_spanning_pass_does_not_jump_to_later_shell_true(self) -> None:
        """A closed subprocess call must not claim a later column-0 shell=True."""
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        source = 'subprocess.run(["ls"], capture_output=True)\n\nos.popen(cmd, shell=True)\n'
        findings = asyncio.run(scanner.scan_file("a.py", source, symbols=[]))
        hits = [f for f in findings if f["kind"] == "subprocess_shell_true"]
        assert hits == []

    # -- JS/TS patterns (#1935 Tier 1) -------------------------------------

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            # Non-literal values: a call, a variable, a member expression.
            ("<div dangerouslySetInnerHTML={{ __html: sanitize(raw) }} />\n", True),
            ("<div dangerouslySetInnerHTML={{ __html: markup }} />\n", True),
            ("<div dangerouslySetInnerHTML={{ __html: props.body }} />\n", True),
            # A SCREAMING_CASE constant is still a non-literal reference and
            # still fires — the pattern cannot tell "this name is pinned in
            # source" from "this name holds a request body" without
            # dataflow. This is the corpus noise the docs page calls out,
            # not a regression.
            ("<div dangerouslySetInnerHTML={{ __html: DOCS_CSS }} />\n", True),
            # A string literal is inert and must not fire. This is the case
            # the positive-lookahead value test exists for: a naive negative
            # lookahead excluding a quote, tried before the preceding
            # whitespace, would let this through.
            ('<div dangerouslySetInnerHTML={{ __html: "<b>ok</b>" }} />\n', False),
            ("<div dangerouslySetInnerHTML={{ __html: '' }} />\n", False),
        ],
    )
    def test_unsafe_inner_html_fires_on_non_literal_values(
        self, source: str, expected: bool
    ) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("component.tsx", source, symbols=[]))
        assert bool([f for f in findings if f["kind"] == "unsafe_inner_html"]) is expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("const q = `SELECT * FROM users WHERE id = ${id}`;\n", True),
            ("const q = `UPDATE users SET name = ${name} WHERE id = ${id}`;\n", True),
            # The bare verb is an ordinary English word without its clause.
            ("const msg = `Order update failed: ${status}`;\n", False),
            ("const cls = `select-none ${active ? 'opacity-50' : ''}`;\n", False),
            # No interpolation: a literal query is not a finding here.
            ("const q = `SELECT * FROM users`;\n", False),
        ],
    )
    def test_template_literal_sql_requires_verb_and_clause(
        self, source: str, expected: bool
    ) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("db.ts", source, symbols=[]))
        assert bool([f for f in findings if f["kind"] == "template_literal_sql"]) is expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("const k = process.env.NEXT_PUBLIC_API_KEY;\n", True),
            ("const k = process.env.NEXT_PUBLIC_N8N_API_KEY;\n", True),
            ("const k = import.meta.env.VITE_SECRET;\n", True),
            ("const k = import.meta.env.VITE_AUTH_TOKEN;\n", True),
            # Public by design, protected by row-level security: excluded
            # rather than flagged, per the corpus noise it made up.
            ("const k = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;\n", False),
            ("const url = process.env.NEXT_PUBLIC_APP_URL;\n", False),
            ("const k = process.env.API_KEY;\n", False),  # not client-exposed
        ],
    )
    def test_public_env_secret_excludes_anon_keys(self, source: str, expected: bool) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("config.ts", source, symbols=[]))
        assert bool([f for f in findings if f["kind"] == "public_env_secret"]) is expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ('const f = new Function("a", "return a + 1");\n', True),
            ("const f = new Function(body);\n", True),
            ("const f = someFunction(a, b);\n", False),
            ("class Function extends Base {}\n", False),
        ],
    )
    def test_new_function_call(self, source: str, expected: bool) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("dynamic.ts", source, symbols=[]))
        assert bool([f for f in findings if f["kind"] == "new_function_call"]) is expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("const agent = new https.Agent({ rejectUnauthorized: false });\n", True),
            ("axios.get(url, { httpsAgent, rejectUnauthorized: false });\n", True),
            ("const agent = new https.Agent({ rejectUnauthorized: true });\n", False),
        ],
    )
    def test_reject_unauthorized_false(self, source: str, expected: bool) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("client.ts", source, symbols=[]))
        assert bool([f for f in findings if f["kind"] == "reject_unauthorized_false"]) is expected


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
                await persist_security_findings(_fake_result({"a.py": SNIPPY}), session, "repo-1")
            async with get_session(sf) as session:
                await persist_security_findings(_fake_result({"a.py": CLEAN}), session, "repo-1")
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
                    SimpleNamespace(file_info=SimpleNamespace(path="auth.py"), symbols=[sym])
                ],
            )
            async with get_session(sf) as session:
                await persist_security_findings(result, session, "repo-1")
            rows = await _rows(sf)
            await engine.dispose()
            return rows

        rows = asyncio.run(_run())
        assert ("auth.py", "security_sensitive_symbol", 7) in rows

    def test_replace_findings_duplicate_key_does_not_lose_rows(self, tmp_path: Path) -> None:
        """Two findings sharing a provenance key must not abort the batch and
        drop the file's other rows (regression for the bulk-INSERT abort that
        swallowed IntegrityError and silently truncated the insert)."""
        from repowise.core.analysis.security_scan import SecurityScanner
        from repowise.core.persistence import get_session

        async def _run():
            engine, sf = await _fresh_session_factory(tmp_path)
            async with get_session(sf) as session:
                scanner = SecurityScanner(session, "repo-1")
                await scanner.replace_findings(
                    {
                        "a.py": [
                            {"kind": "hardcoded_password", "severity": "high", "line": 1},
                            {"kind": "security_sensitive_symbol", "severity": "low", "line": 7},
                            {"kind": "security_sensitive_symbol", "severity": "low", "line": 7},
                        ],
                    },
                    ["a.py"],
                )
            rows = await _rows(sf)
            await engine.dispose()
            return rows

        rows = asyncio.run(_run())
        assert sorted(rows) == [
            ("a.py", "hardcoded_password", 1),
            ("a.py", "security_sensitive_symbol", 7),
        ]

    def test_replace_findings_rescan_keeps_prior_and_new_rows(self, tmp_path: Path) -> None:
        """Re-running replace_findings over the same file must yield the same
        full row set — duplicates on the provenance key are no-ops, never a
        partial insert."""
        from repowise.core.analysis.security_scan import SecurityScanner
        from repowise.core.persistence import get_session

        async def _run():
            engine, sf = await _fresh_session_factory(tmp_path)
            findings = {
                "a.py": [
                    {"kind": "hardcoded_password", "severity": "high", "line": 1},
                    {"kind": "security_sensitive_symbol", "severity": "low", "line": 7},
                    {"kind": "security_sensitive_symbol", "severity": "low", "line": 7},
                ],
            }
            for _ in range(2):
                async with get_session(sf) as session:
                    scanner = SecurityScanner(session, "repo-1")
                    await scanner.replace_findings(findings, ["a.py"])
            rows = await _rows(sf)
            await engine.dispose()
            return rows

        rows = asyncio.run(_run())
        assert sorted(rows) == [
            ("a.py", "hardcoded_password", 1),
            ("a.py", "security_sensitive_symbol", 7),
        ]
