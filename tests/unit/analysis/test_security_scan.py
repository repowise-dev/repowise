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
from typing import ClassVar

import pytest
from sqlalchemy import text

from repowise.core.analysis.security_scan import (
    SecurityScanner,
    _redaction,
    scan_source,
    scan_source_map,
)

SNIPPY = b"""import pickle

password = 'super_secret_password_123'
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
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("obj = pickle.loads(request.body)\n", True),
            ("obj = pickle.loads(blob); copy = pickle.dumps(obj)\n", True),
            ("assert pickle.loads(pickle.dumps(config)) == config\n", False),
            ("gb2 = pickle.loads(pickle.dumps(gb, protocol=pickle.HIGHEST_PROTOCOL))\n", False),
        ],
    )
    def test_pickle_round_trip_is_not_a_load(self, source: str, expected: bool) -> None:
        assert bool([f for f in scan_source("a.py", source) if f["kind"] == "pickle_loads"]) is expected

    def test_line_patterns_fire_on_real_source(self) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("a.py", SNIPPY.decode(), symbols=[]))
        kinds = {f["kind"] for f in findings}
        assert "hardcoded_password" in kinds
        assert "pickle_loads" in kinds
        assert "eval_call" in kinds
        by_kind = {f["kind"]: f for f in findings}
        assert by_kind["hardcoded_password"]["line"] == 3
        # The raw secret value must NOT appear in the snippet.
        pw_snippet = by_kind["hardcoded_password"]["snippet"]
        assert "super_secret_password_123" not in pw_snippet, (
            f"secret value leaked into snippet: {pw_snippet!r}"
        )
        # The masked form must be present.
        assert "supe****" in pw_snippet, (
            f"expected masked form 'supe****' in snippet: {pw_snippet!r}"
        )

    def test_clean_source_yields_nothing(self) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("b.py", CLEAN.decode(), symbols=[]))
        assert findings == []

    @pytest.mark.parametrize(
        "allowed",
        [
            'password = "actualcredential123"  # pragma: allowlist secret\n',
            '# pragma: allowlist nextline secret\npassword = "actualcredential123"\n',
        ],
    )
    def test_detect_secrets_pragmas_exclude_findings_before_storage(self, allowed: str) -> None:
        source = allowed + 'password = "anothercredential456"\n'
        findings = scan_source("config.py", source)
        assert [(f["kind"], f["line"]) for f in findings] == [
            ("hardcoded_password", len(allowed.splitlines()) + 1)
        ]

    def test_detect_secrets_pragma_inside_a_string_does_not_allowlist(self) -> None:
        source = 'password = "actualcredential123 # pragma: allowlist secret"\n'
        assert [(f["kind"], f["line"]) for f in scan_source("config.py", source)] == [
            ("hardcoded_password", 1)
        ]

    def test_detect_secrets_pragma_does_not_hide_non_secret_calls(self) -> None:
        source = 'password = "actualcredential123"; eval(payload)  # pragma: allowlist secret\n'
        assert [(f["kind"], f["line"]) for f in scan_source("config.py", source)] == [
            ("eval_call", 1)
        ]

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
            # execFile passes argv and runs no shell, like subprocess without shell=True.
            ('import { execFile } from "node:child_process";\nexecFile(bin, args);\n', set()),
            ('import { exec as run } from "child_process";\nrun(cmd);\n', {"exec_call"}),
            ('const { execSync: sh } = require("child_process");\nsh(cmd);\n', {"exec_call"}),
            ('import * as cp from "node:child_process";\ncp.execFile(bin);\n', set()),
            ('import * as cp from "node:child_process";\ncp.exec(cmd);\n', {"exec_call"}),
            ('require("child_process").exec(cmd);\n', {"exec_call"}),
            # A regex's ``.exec`` in a file that also spawns is not bound to the module.
            ('require("child_process");\nconst m = /a/.exec(s);\n', set()),
            # The file's own function named exec, beside an unrelated spawn import.
            (
                'import { spawn } from "node:child_process";\n'
                "function exec(args) { return spawn(bin, args); }\n"
                "exec(args);\n",
                set(),
            ),
            ('import * as cp from "child_process";\n// cp.exec(cmd)\n', set()),
        ],
    )
    def test_js_exec_is_reported_only_when_child_process_is_present(
        self, source: str, expected: set[str]
    ) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("spawn.ts", source, symbols=[]))
        assert {row["kind"] for row in findings if row["kind"].endswith("_call")} == expected

    def test_combined_prefilter_leaves_calls_to_the_call_scan(self) -> None:
        from repowise.core.analysis.security_scan import _ANY_PATTERN

        assert not _ANY_PATTERN.search("eval (")
        assert not _ANY_PATTERN.search("window.exec(")
        assert _ANY_PATTERN.search("os.system(cmd)")

    @pytest.mark.parametrize(
        ("source", "code", "uncommented"),
        [
            ('x = "a\\"b"  # c\ny', "x =            \ny", 'x = "a\\"b"     \ny'),
            ("a /* b\nc */ d", "a     \n     d", "a     \n     d"),
            ("s = '''q\n\"\"\" ''' + k", "s =     \n        + k", "s = '''q\n\"\"\" ''' + k"),
            ('t = `a${f({b: "c"})}d` // e', "t =     f({b:     )        ", 't = `a${f({b: "c"})}d`     '),
            ('open "abc\\', "open      ", 'open "abc\\'),
            ("u = '#' # z", "u =        ", "u = '#'    "),
        ],
    )
    def test_masking_blanks_comments_and_strings_in_place(
        self, source: str, code: str, uncommented: str
    ) -> None:
        from repowise.core.analysis.security_scan import mask_comments_and_strings

        assert mask_comments_and_strings(source) == code
        assert mask_comments_and_strings(source, strings=False) == uncommented

    def test_file_without_a_call_word_skips_the_call_scan(self) -> None:
        from repowise.core.analysis.security_scan import _call_findings

        def unreachable() -> str:
            raise AssertionError("masked source built for a file with no call")

        assert _call_findings("a.ts", "run(executor);\n", unreachable) == []
        assert _call_findings("a.py", "x = 1\n", unreachable) == []

    @pytest.mark.parametrize(
        "source",
        [
            'const API_KEY = "abc123456789";\n',
            "const N8N_API_KEY = 'abc123456789';\n",
            'SECRET = "abc123456789"\n',
            'PASSWORD = "abc123456789"\n',
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

    @pytest.mark.parametrize(
        "source",
        [
            'password = ""\n',
            "password = 'short'\n",
            'password = "hunter2"\n',
            'api_key = "1234567"\n',
        ],
    )
    def test_credential_value_length_gate(self, source: str) -> None:
        """Credentials shorter than 8 characters yield no findings."""
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("config.py", source, symbols=[]))
        assert not any(f["kind"] in {"hardcoded_password", "hardcoded_secret"} for f in findings)

    @pytest.mark.parametrize(
        "source",
        [
            'password = "dummy_secret_value_123"\n',
            'password = "changeme_secret_123"\n',
            'password = "changeit"\n',
            'password = "password"\n',
            'password = "placeholder_secret"\n',
            'api_key = "sk-..."\n',
            'api_key = "your-api-key-here"\n',
            'api_key = "<YOUR_API_KEY>"\n',
            'password = "fixture-value"\n',
            'api_key = "«redacted:sk-repo»"\n',
            'OPENAI_API_KEY = "[REDACTED]-k3y-0f-mine"\n',
        ],
    )
    def test_credential_placeholder_filtering(self, source: str) -> None:
        """Values containing placeholder tokens yield no findings."""
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("config.py", source, symbols=[]))
        assert not any(f["kind"] in {"hardcoded_password", "hardcoded_secret"} for f in findings)

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("auth", True),
            ("Password", True),
            ("jwt", True),
            ("crypto", True),
            # A database session, a pytest fixture, a lexer token.
            ("session", False),
            ("SESSION", False),
            ("Token", False),
            ("get_token", False),
        ],
    )
    def test_security_sensitive_symbol_names(self, name: str, expected: bool) -> None:
        sym = SimpleNamespace(name=name, start_line=3)
        kinds = {f["kind"] for f in scan_source("m.py", "", [sym])}
        assert ("security_sensitive_symbol" in kinds) is expected

    @pytest.mark.parametrize(
        "file_path",
        [
            "tests/unit/test_auth.py",
            "tests\\unit\\test_auth.py",
            "test_main.py",
            "fixtures/keys.py",
            "spec/helpers.rb",
            "mocks/auth_mock.ts",
            "examples/demo.py",
        ],
    )
    def test_credential_path_severity_downgrade(self, file_path: str) -> None:
        """Valid credentials in test/fixture paths are downgraded to low severity."""
        source = 'password = "super_secret_real_password_99"\n'
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file(file_path, source, symbols=[]))
        hits = [f for f in findings if f["kind"] in {"hardcoded_password", "hardcoded_secret"}]
        assert len(hits) == 1
        assert hits[0]["severity"] == "low"

    # -- Vendor credential shapes (#2117) ----------------------------------

    @pytest.mark.parametrize(
        ("source", "expected_kind"),
        [
            ('AWS_ACCESS_KEY_ID = "AKIAQZXNRTVYWMPKLBHG"\n', "aws_access_key"),  # pragma: allowlist secret
            (
                'GITHUB_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyzABCDEFGHIJ"\n',  # pragma: allowlist secret
                "github_token",
            ),
            (
                "GH_APP_TOKEN = 'ghu_abcdefghijklmnopqrstuvwxyzABCDEFGHIJ'\n",  # pragma: allowlist secret
                "github_token",
            ),
            (
                'GH_FINE_GRAINED = "github_pat_'
                + "abcdefghijklmnopqrstuvwxyzABCDEFGHIJabcdefghijklmnopqrstuvwxyzABCDEFGHIJabcdefghij"
                + '"\n',
                "github_token",
            ),
            ('SLACK_BOT_TOKEN = "xoxb-4721609583-T5Rk9mPz2Qa"\n', "slack_token"),  # pragma: allowlist secret
            (
                'GOOGLE_MAPS_API_KEY = "AIzaSyD9K2vQ7xR4mZ1pL8tY6wU3nB0cF5hD9aX"\n',  # pragma: allowlist secret
                "google_api_key",
            ),
            (
                # Built from parts so the literal "sk_" + "live_" prefix pair
                # never appears contiguous in source: GitHub's push
                # protection flags that Stripe prefix on sight, independent
                # of body entropy.
                'stripe.api_key = "' + "sk_" + "live_" + "A" * 24 + '"\n',
                "stripe_key",
            ),
        ],
    )
    def test_vendor_key_shapes_fire_regardless_of_variable_name(
        self, source: str, expected_kind: str
    ) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("config.py", source, symbols=[]))
        kinds = {f["kind"] for f in findings}
        assert expected_kind in kinds

    def test_token_and_access_key_keywords_are_detected(self) -> None:
        """#2117: ``token`` and ``access_key`` were not in the keyword list."""
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        for source in (
            'TOKEN = "a_real_looking_token_value_123"\n',
            'access_key = "a_real_looking_access_key_456"\n',
        ):
            findings = asyncio.run(scanner.scan_file("config.py", source, symbols=[]))
            assert any(f["kind"] == "hardcoded_secret" for f in findings), source

    def test_client_id_vendor_key_is_caught_by_shape_not_name(self) -> None:
        """A vendor key is invisible to the keyword list under a name like
        ``client_id``, so it must be caught by the value shape alone."""
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        source = 'client_id = "AKIAQZXNRTVYWMPKLBHG"\n'  # pragma: allowlist secret
        findings = asyncio.run(scanner.scan_file("config.py", source, symbols=[]))
        assert any(f["kind"] == "aws_access_key" for f in findings)

    def test_pem_private_key_with_body_is_flagged(self) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        source = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj\n"
            "-----END RSA PRIVATE KEY-----\n"
        )
        findings = asyncio.run(scanner.scan_file("id_rsa.py", source, symbols=[]))
        hits = [f for f in findings if f["kind"] == "private_key_pem"]
        assert len(hits) == 1
        assert hits[0]["line"] == 1

    def test_pem_header_without_body_does_not_fire(self) -> None:
        """Code that assembles PEM text from the header string alone (no key
        material) must not be reported."""
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        source = (
            'PEM_HEADER = "-----BEGIN RSA PRIVATE KEY-----"\n'
            'PEM_FOOTER = "-----END RSA PRIVATE KEY-----"\n'
        )
        findings = asyncio.run(scanner.scan_file("pem_templates.py", source, symbols=[]))
        assert not any(f["kind"] == "private_key_pem" for f in findings)

    def test_vendor_key_shapes_are_gated_by_credential_value_rules(self) -> None:
        """#2121's value gate must apply to the new vendor kinds too: a
        low-severity path downgrades severity, same as the keyword kinds."""
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        source = 'AWS_ACCESS_KEY_ID = "AKIAQZXNRTVYWMPKLBHG"\n'  # pragma: allowlist secret
        findings = asyncio.run(scanner.scan_file("tests/fixtures/keys.py", source, symbols=[]))
        hits = [f for f in findings if f["kind"] == "aws_access_key"]
        assert len(hits) == 1
        assert hits[0]["severity"] == "low"

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
            ("// never use new Function(body) here\n", False),
            ('const doc = "new Function(body)";\n', False),
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
            ('const doc = "TLS off (rejectUnauthorized: false).";\n', False),
            ("// rejectUnauthorized: false would disable TLS checks\n", False),
        ],
    )
    def test_reject_unauthorized_false(self, source: str, expected: bool) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("client.ts", source, symbols=[]))
        assert bool([f for f in findings if f["kind"] == "reject_unauthorized_false"]) is expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("import requests\nrequests.get(url, verify=False)\n", True),
            ('"tls_verify_false": "TLS verification turned off (verify=False).",\n', False),
            ("# verify=False disables certificate checks\n", False),
        ],
    )
    def test_tls_verify_false_ignores_prose(self, source: str, expected: bool) -> None:
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("client.py", source, symbols=[]))
        assert bool([f for f in findings if f["kind"] == "tls_verify_false"]) is expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("import httpx\nhttpx.Client(\n    base_url=url,\n    verify=False,\n)\n", True),
            ('import boto3\nboto3.client("s3", verify=False)\n', True),
            ('import boto3\nc = boto3.session.Session().client("s3", verify=False)\n', True),
            ("from requests import get\nget(url, verify=False)\n", True),
            ("import requests\ns = requests.Session()\ns.get(url, verify=False)\n", True),
            ("import requests as r\nwith r.Session() as s:\n    s.post(url, verify=False)\n", True),
            ("import requests\nclass C:\n    def go(self):\n        self.http.get(u, verify=False)\n", True),
            ("import requests\ns = requests.Session()\ns.verify = False\n", True),
            ("import requests\nclass Backend:\n    verify = False\n", True),
            # A file with no HTTP client library: whatever ``get`` or ``client`` is, not TLS.
            ("rows = db.get(key, verify=False)\n", False),
            ('c = client("s3", verify=False)\n', False),
            ("session.verify = False\n", False),
            ("class Backend:\n    verify = False\n", False),
            # An HTTP library imported, but the receiver is some other object.
            ("import requests\nrows = db.get(key, verify=False)\n", False),
            ("import requests\nGitRepo(io, git_commit_verify=False)\n", False),
            ("import requests\nmock_get.assert_called_with(url, verify=False)\n", False),
            # A function of the project's own whose keyword means something else.
            ("import httpx\nrows = load_fix_first(session, repo_id, verify=False)\n", False),
        ],
    )
    def test_tls_verify_false_needs_an_http_client(self, source: str, expected: bool) -> None:
        findings = scan_source("client.py", source)
        assert bool([f for f in findings if f["kind"] == "tls_verify_false"]) is expected

    def test_tls_verify_false_keeps_the_line_match_when_python_does_not_parse(self) -> None:
        findings = scan_source("client.py", "def broken(:\nload(x, verify=False)\n")
        assert [f["line"] for f in findings if f["kind"] == "tls_verify_false"] == [2]

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("rows = con.execute(f\"SELECT id FROM users WHERE name = '{name}'\")\n", True),
            ('sql = (\n    f"SELECT {cols}, name "\n    f"FROM users WHERE id = {uid}"\n)\n', True),
            ("q = f\"SELECT * FROM docs WHERE title LIKE '%{term}%'\"\n", True),
            ('q = f"SELECT * FROM t WHERE age >= {age} ORDER BY 1"\n', True),
            # Identifiers, fragments and placeholder lists, which cannot be bound.
            ('found = con.execute(f"SELECT MAX(at) FROM {table}").fetchone()\n', False),
            ('q = f"SELECT ref FROM omissions WHERE ref IN ({placeholders})"\n', False),
            ('q = f"SELECT {column}, COUNT(*) FROM episodes{where} GROUP BY {column}"\n', False),
            ('q = f"SELECT id FROM t WHERE id = :id", params={"id": 1}\n', False),
            ('q = f"SELECT a FROM t WHERE b = {{literal}}"\n', False),
            ('q = f"""SELECT id FROM users WHERE id = {uid}"""\n', True),
            ('q = f"""SELECT {cols}\n    FROM users\n    WHERE name = \'{name}\'\n"""\n', True),
            ('q = f"""SELECT id FROM {table}"""\nx = {"a": 1}\n', False),
            # A message about SQL, with no query in it.
            ('reason = f"SELECT * inside a {kind}: the shape changes"\n', False),
            # Python source quoted inside a string, as a test fixture holds it.
            ("src = 'q = f\"SELECT * FROM t WHERE id = {uid}\"'\n", False),
        ],
    )
    def test_fstring_sql_needs_a_value_written_into_a_query(self, source: str, expected: bool) -> None:
        findings = scan_source("db.py", source)
        assert bool([f for f in findings if f["kind"] == "fstring_sql"]) is expected

    @pytest.mark.parametrize(
        ("line", "kind"),
        [
            ("<div dangerouslySetInnerHTML={{ __html: markup }} />", "unsafe_inner_html"),
            ("const q = `SELECT * FROM users WHERE id = ${id}`;", "template_literal_sql"),
            ("const f = new Function(body);", "new_function_call"),
            ("const a = new https.Agent({ rejectUnauthorized: false });", "reject_unauthorized_false"),
            ("const k = process.env.NEXT_PUBLIC_API_KEY;", "public_env_secret"),
        ],
    )
    def test_javascript_kinds_fire_in_javascript_not_python(self, line: str, kind: str) -> None:
        assert kind in {f["kind"] for f in scan_source("web/app.tsx", line + "\n")}
        quoted = f"CASE = {line!r}\n"
        assert kind not in {f["kind"] for f in scan_source("src/check.py", quoted)}

    @pytest.mark.parametrize(
        ("path", "source", "expected"),
        [
            ("a.py", "digest = hashlib.md5(data).hexdigest()\n", True),
            ("a.py", "mac = hmac.new(key, msg, hashlib.sha1)\n", True),
            ("a.py", 'h = hashlib.new("md5")\n', True),
            ("a.py", "from hashlib import sha1\nkey = sha1(raw)\n", True),
            ("a.js", 'const h = crypto.createHash("md5");\n', True),
            ("A.java", 'MessageDigest md = MessageDigest.getInstance("SHA-1");\n', True),
            ("a.go", "sum := md5.Sum(data)\n", True),
            ("A.cs", "using var h = MD5.Create();\n", True),
            ("a.rb", "Digest::SHA1.hexdigest(s)\n", True),
            ("a.rs", "let d = md5::compute(bytes);\n", True),
            ("a.php", "$h = md5($password);\n", True),
            # A commit id named for its algorithm, and the word in prose.
            ("a.py", 'sha1 = git("rev-parse", "HEAD")\n', False),
            ("a.py", "assert repo.head_commit == sha1\n", False),
            ("a.py", "#: A commit id (sha1 or sha256).\n", False),
            ("a.py", '"weak_hash": "The words md5 or sha1.",\n', False),
            ("a.py", "_commit(repo, \"h.py\", \"hashlib.md5(b'')\")\n", False),
            # Declared a checksum, not a security control.
            ("a.py", "key = hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()\n", False),
            ("a.py", "key = hashlib.sha1(\n    text.encode(), usedforsecurity=False\n)\n", False),
            ("a.py", 'key = hashlib.sha1(f"({a})".encode(), usedforsecurity=False)\n', False),
            ("a.py", 'key = hashlib.sha1(b"usedforsecurity=False")\n', True),
            ("a.py", "key = hashlib.sha1(\n    text.encode()\n)\nok = sha256(b, usedforsecurity=False)\n", True),
            # Cut to a few characters where it is computed, or kept as a key: an id.
            ("a.py", "short = hashlib.md5(name.encode()).hexdigest()[:8]\n", False),
            ("a.py", "short = hashlib.sha1(name.encode()).hexdigest()[0:12]\n", False),
            ("a.js", 'const id = crypto.createHash("md5").update(s).digest("hex").slice(0, 8);\n', False),
            ("a.py", "etag = hashlib.md5(body).hexdigest()\n", False),
            ("a.py", "cache_key = hashlib.sha1(url.encode()).hexdigest()\n", False),
            ("a.ts", 'const cacheKey = createHash("md5").update(url).digest("hex");\n', False),
            ("a.py", 'slug = title + "-" + md5(title.encode()).hexdigest()\n', False),
            # A credential stays a finding, truncated or named as a key.
            ("a.py", "pw = hashlib.md5(password.encode()).hexdigest()[:8]\n", True),
            ("a.py", "cache_key = hashlib.md5(token).hexdigest()\n", True),
            ("a.py", "digest = hashlib.md5(data).hexdigest()[8:]\n", True),
            ("a.py", "full = hashlib.md5(data).digest()\n", True),
            ("a.py", "pw = hashlib.md5(p.encode()).hexdigest()[:8]\n", True),
            ("a.py", "whole = hashlib.md5(data).hexdigest()[:32]\n", True),
            ("a.py", "mac = hmac.new(k, m, hashlib.md5).hexdigest()[:8]\n", True),
            ("a.py", "if cache_key in seen: h = hashlib.md5(data).hexdigest()\n", True),
            ("a.ts", 'const cacheKey: string = createHash("md5").update(url).digest("hex");\n', False),
            ("a.py", 'row = {"etag": hashlib.md5(body).hexdigest()}\n', False),
            ("a.py", "author_slug = hashlib.md5(name.encode()).hexdigest()\n", False),
        ],
    )
    def test_weak_hash_is_an_algorithm_in_use(self, path: str, source: str, expected: bool) -> None:
        assert bool([f for f in scan_source(path, source) if f["kind"] == "weak_hash"]) is expected


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


class TestSecretMasking:
    """Credential snippets must be masked; non-credential snippets must not."""

    @pytest.mark.parametrize(
        ("val", "expected"),
        [
            ("super_secret_pass_99", "supe****"),
            ("abcd", "abcd****"),
            ("abc", "****"),
        ],
    )
    def test_redaction_keeps_four_chars(self, val: str, expected: str) -> None:
        assert _redaction(val) == expected

    # ------------------------------------------------------------------ #
    # scan_file — credential kinds are masked                              #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        ("source", "kind", "raw_val"),
        [
            (
                "password = 'super_secret_password_123'\n",
                "hardcoded_password",
                "super_secret_password_123",
            ),
            (
                'API_KEY = "abc123456789"\n',
                "hardcoded_secret",
                "abc123456789",
            ),
            (
                'SECRET = "abcd1234"\n',
                "hardcoded_secret",
                "abcd1234",
            ),
        ],
    )
    def test_credential_snippet_is_masked(
        self, source: str, kind: str, raw_val: str
    ) -> None:
        """The plaintext secret value must not appear in any snippet."""
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("config.py", source, symbols=[]))
        hits = [f for f in findings if f["kind"] == kind]
        assert hits, f"no finding of kind {kind!r} produced"
        for hit in hits:
            assert raw_val not in hit["snippet"], (
                f"plaintext secret leaked into snippet for {kind!r}: {hit['snippet']!r}"
            )
            # Masked form must be present: first 4 chars + ****.
            expected_mask = raw_val[:4] + "****"
            assert expected_mask in hit["snippet"], (
                f"expected masked form {expected_mask!r} not found in {hit['snippet']!r}"
            )

    def test_credential_finding_carries_no_raw_value_field(self) -> None:
        """A finding holds only the persisted keys, so no raw value rides along."""
        source = "password = 'super_secret_password_123'\n"
        for f in scan_source("config.py", source):
            assert set(f) == {"kind", "severity", "snippet", "line"}

    # ------------------------------------------------------------------ #
    # scan_file — non-credential kinds are NOT masked                      #
    # ------------------------------------------------------------------ #

    def test_non_credential_snippet_is_not_masked(self) -> None:
        """eval_call and pickle_loads snippets must be the raw matched line."""
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(scanner.scan_file("a.py", SNIPPY.decode(), symbols=[]))
        by_kind = {f["kind"]: f for f in findings}
        # eval_call snippet should contain the raw text.
        assert "eval" in by_kind["eval_call"]["snippet"]
        assert "****" not in by_kind["eval_call"]["snippet"]
        # pickle_loads snippet should contain the raw text.
        assert "pickle" in by_kind["pickle_loads"]["snippet"]
        assert "****" not in by_kind["pickle_loads"]["snippet"]

    def test_severity_downgrade_still_masks_credential(self) -> None:
        """Even when severity is downgraded (test-path), the snippet is masked."""
        source = "password = 'super_secret_real_password_99'\n"
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        findings = asyncio.run(
            scanner.scan_file("tests/unit/test_auth.py", source, symbols=[])
        )
        hits = [f for f in findings if f["kind"] == "hardcoded_password"]
        assert hits, "finding must still fire on test path (just downgraded)"
        assert hits[0]["severity"] == "low"
        assert "super_secret_real_password_99" not in hits[0]["snippet"]
        assert "supe****" in hits[0]["snippet"]


class TestScanSource:
    """The session-free entry points, and masking that survives the length cap."""

    def test_long_token_is_masked_before_the_snippet_is_trimmed(self) -> None:
        """A value running past the 120-char cut must not ship its head raw."""
        token = "".join(chr(ord("a") + (i * 7) % 26) for i in range(220))
        findings = scan_source("config.py", f'API_KEY = "{token}"\n')
        hits = [f for f in findings if f["kind"] == "hardcoded_secret"]
        assert hits
        snippet = hits[0]["snippet"]
        assert len(snippet) <= 120
        assert token[:4] + "****" in snippet
        leaked = [token[i : i + 5] for i in range(len(token) - 4) if token[i : i + 5] in snippet]
        assert not leaked, f"raw token substrings in snippet: {leaked[:3]}"

    def test_recurring_value_is_masked_everywhere_on_the_line(self) -> None:
        source = "password = 'hunter2hunter2'  # was hunter2hunter2\n"
        (hit,) = [f for f in scan_source("a.py", source) if f["kind"] == "hardcoded_password"]
        assert "hunter2hunter2" not in hit["snippet"]

    @pytest.mark.parametrize(
        ("source", "raw"),
        [
            ("NEXT_PUBLIC_STRIPE_SECRET_KEY=sk_live_0123456789abcdef\n", "sk_live_0123456789abcdef"),  # pragma: allowlist secret
            ('  "VITE_API_KEY": "abcdef0123456789",\n', "abcdef0123456789"),
            ("export const NEXT_PUBLIC_AUTH_TOKEN = 'tok_9876543210';\n", "tok_9876543210"),
        ],
    )
    def test_public_env_secret_value_is_redacted(self, source: str, raw: str) -> None:
        hits = [f for f in scan_source("config.ts", source) if f["kind"] == "public_env_secret"]
        assert hits, "the finding must still be emitted"
        assert raw not in hits[0]["snippet"]
        assert raw[:4] + "****" in hits[0]["snippet"]

    def test_public_env_secret_without_a_value_is_unchanged(self) -> None:
        source = "const k = process.env.NEXT_PUBLIC_N8N_API_KEY;\n"
        (hit,) = [f for f in scan_source("config.ts", source) if f["kind"] == "public_env_secret"]
        assert hit["snippet"] == source.strip()

    def test_secret_kinds_and_history_gate_are_unchanged(self) -> None:
        from repowise.core.analysis.history_scan import HistorySecurityScanner
        from repowise.core.analysis.security_scan import SECRET_KINDS

        assert (
            frozenset(
                {
                    "hardcoded_password",
                    "hardcoded_secret",
                    "aws_access_key",
                    "github_token",
                    "slack_token",
                    "google_api_key",
                    "stripe_key",
                    "gitlab_token",
                    "azure_devops_pat",
                    "git_url_credentials",
                    "private_key_pem",
                }
            )
            == SECRET_KINDS
        )
        assert not HistorySecurityScanner._passes_gate("public_env_secret", secrets_only=True)

    def test_scan_file_wraps_scan_source(self) -> None:
        source = SNIPPY.decode()
        scanner = SecurityScanner(session=None, repo_id="r1")  # type: ignore[arg-type]
        sym = SimpleNamespace(name="auth", start_line=2)
        assert asyncio.run(scanner.scan_file("a.py", source, [sym])) == scan_source(
            "a.py", source, [sym]
        )

    def test_scan_source_map_returns_replace_findings_inputs(self) -> None:
        result = _fake_result({"a.py": SNIPPY, "clean.py": CLEAN, "s.py": "x = 1\n"})
        findings_by_file, scanned = scan_source_map(result.parsed_files, result.source_map)
        assert scanned == ["a.py", "clean.py", "s.py"]
        assert set(findings_by_file) == {"a.py"}
        assert findings_by_file["a.py"] == scan_source("a.py", SNIPPY.decode())

    def test_ignore_marker_silences_the_stored_scan(self) -> None:
        source = (
            "data = pickle.loads(blob)  # repowise-security-ignore: pickle_loads\n"
            "other = pickle.loads(raw)  # repowise-security-ignore: eval_call\n"
            "os.system(cmd)  # repowise-security-ignore\n"
            "os.system(cmd2)\n"
        )
        # The gate reads scan_source and reports silenced findings as suppressed.
        assert len(scan_source("a.py", source)) == 4
        result = _fake_result({"a.py": source})
        findings_by_file, _ = scan_source_map(result.parsed_files, result.source_map)
        assert sorted((f["kind"], f["line"]) for f in findings_by_file["a.py"]) == [
            ("os_system", 4),
            ("pickle_loads", 2),
        ]

    def test_a_fully_silenced_file_stores_nothing(self) -> None:
        source = "data = pickle.loads(blob)  # repowise-security-ignore\n"
        result = _fake_result({"a.py": source})
        findings_by_file, scanned = scan_source_map(result.parsed_files, result.source_map)
        assert scanned == ["a.py"] and findings_by_file == {}


def _assert_no_raw(snippet: str, raw: str) -> None:
    leaked = [raw[i : i + 5] for i in range(len(raw) - 4) if raw[i : i + 5] in snippet]
    assert not leaked, f"raw value in {snippet!r}"


class TestEverySnippetIsMasked:
    """A secret leaks through whichever finding on its line shows the line."""

    PW, KEY = "Qz8vK2mW9xL4pR7t", "Hj3nB6cX1vM5kS8d"

    def test_two_secrets_on_one_line(self) -> None:
        line = f'password = "{self.PW}"; api_key = "{self.KEY}"\n'
        findings = scan_source("a.py", line)
        assert {f["kind"] for f in findings} >= {"hardcoded_password", "hardcoded_secret"}
        for f in findings:
            _assert_no_raw(f["snippet"], self.PW)
            _assert_no_raw(f["snippet"], self.KEY)

    @pytest.mark.parametrize(
        ("source", "kind"),
        [
            (f'API_KEY = "{KEY}"; h = md5(b)\n', "weak_hash"),
            (f'os.system(cmd); secret = "{KEY}"\n', "os_system"),
            (f'x = eval(y); secret = "{KEY}"\n', "eval_call"),
            (f'subprocess.run(cmd, secret = "{KEY}",\n    shell=True)\n', "subprocess_shell_true"),
        ],
    )
    def test_secret_beside_another_pattern(self, source: str, kind: str) -> None:
        (hit,) = [f for f in scan_source("a.py", source) if f["kind"] == kind]
        _assert_no_raw(hit["snippet"], self.KEY)

    def test_two_public_env_values_on_one_line(self) -> None:
        line = "NEXT_PUBLIC_API_KEY=abcdef0123456789 VITE_AUTH_TOKEN=tok_9876543210\n"
        (hit,) = [f for f in scan_source("a.env.ts", line) if f["kind"] == "public_env_secret"]
        _assert_no_raw(hit["snippet"], "abcdef0123456789")
        _assert_no_raw(hit["snippet"], "tok_9876543210")

    def test_unterminated_value_with_trailing_space(self) -> None:
        (hit,) = scan_source("a.py", f'password = "{self.PW}   \n')
        _assert_no_raw(hit["snippet"], self.PW)

    @pytest.mark.parametrize("pad", range(95, 120))
    def test_trim_never_ends_inside_a_mask(self, pad: int) -> None:
        from repowise.server.services.security_lines import check_finding_line

        line = f'{"x" * pad} password = "{self.PW}"'
        (hit,) = [f for f in scan_source("a.py", line + "\n") if f["kind"] == "hardcoded_password"]
        assert not hit["snippet"].endswith(("*", "**", "***")) or hit["snippet"].endswith("****")
        _assert_no_raw(hit["snippet"], self.PW)
        assert check_finding_line([line], 1, hit["snippet"], hit["kind"]).verified

    def test_a_repeated_value_masks_in_linear_time(self) -> None:
        import time

        sources = [
            " ".join(f'password = "{self.PW}";' for _ in range(count)) + "\n"
            for count in (2500, 10000)
        ]
        timings: list[list[float]] = [[], []]
        # Alternate sizes and take the best of three runs to reduce scheduling noise.
        for _ in range(3):
            for source, samples in zip(sources, timings, strict=True):
                started = time.perf_counter()
                (hit,) = scan_source("a.py", source)
                samples.append(time.perf_counter() - started)
                _assert_no_raw(hit["snippet"], self.PW)

        small, large = (min(samples) for samples in timings)
        # A 4x input should take about 4x as long, not the 16x of quadratic work.
        assert large < 8 * small, f"4x input took {large / small:.2f}x as long"

    def test_long_github_pat_straddling_the_cut_is_masked(self) -> None:
        pat = "github_pat_" + "".join(chr(ord("A") + (i * 7) % 26) for i in range(82))
        line = f'{"x" * 80} = Client(auth="{pat}")'
        assert line.index(pat) < 120 < line.index(pat) + len(pat)
        (hit,) = [f for f in scan_source("a.py", line + "\n") if f["kind"] == "github_token"]
        assert "gith****" in hit["snippet"]
        _assert_no_raw(hit["snippet"], pat)

    def test_vendor_token_beside_a_weak_hash_is_masked_in_both(self) -> None:
        token = "ghp_" + "Kq7mZ2xV9pL4rT8wN3bY6cH1jF5dS0gA2eUo"
        findings = scan_source("a.py", f'digest = md5(body); auth = "{token}"\n')
        assert {f["kind"] for f in findings} == {"weak_hash", "github_token"}
        for f in findings:
            _assert_no_raw(f["snippet"], token)

    def test_inline_pem_body_is_masked(self) -> None:
        body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj"
        line = (
            '  "private_key": "-----BEGIN PRIVATE KEY-----\\n'
            f'{body}\\n{body[::-1]}\\n-----END PRIVATE KEY-----\\n",'
        )
        (hit,) = [f for f in scan_source("sa.json", line + "\n") if f["kind"] == "private_key_pem"]
        _assert_no_raw(hit["snippet"], body)
        _assert_no_raw(hit["snippet"], body[::-1])

    def test_stray_hit_on_a_pem_body_line_is_masked(self) -> None:
        # pragma: allowlist nextline secret
        body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgE+AKIAQ2Z7RT4WXY5BCDFG/AoIBAQC7VJTUt9Us8cKj"
        source = f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----\n"
        (hit,) = [f for f in scan_source("id_rsa", source) if f["kind"] == "aws_access_key"]
        assert hit["line"] == 2
        _assert_no_raw(hit["snippet"], body)


class TestVendorShapeEdges:
    """Edges of the vendor-shape and keyword patterns."""

    @staticmethod
    def _kinds(source: str, path: str = "config.py") -> list[str]:
        return [f["kind"] for f in scan_source(path, source)]

    @pytest.mark.parametrize("prefix", ["sk_live_", "rk_live_", "sk_test_", "sk_prod_"])
    def test_stripe_secret_and_restricted_keys_fire(self, prefix: str) -> None:
        # Split so the contiguous prefix never sits in source for push protection.
        key = prefix[:3] + prefix[3:] + "A" * 24
        assert "stripe_key" in self._kinds(f'stripe_client = Stripe("{key}")\n')

    def test_stripe_publishable_key_does_not_fire(self) -> None:
        key = "pk_" + "live_" + "A" * 24
        assert "stripe_key" not in self._kinds(f'const stripe = loadStripe("{key}");\n', "a.ts")

    @pytest.mark.parametrize(
        "source",
        [
            'ACCESS_TOKEN = "access_token"\n',
            'const token = "auth_token_key";\n',
            'token = "{{ csrf_token }}"\n',
            'API_KEY = "${STRIPE_API_KEY}"\n',
        ],
    )
    def test_key_names_and_templates_are_not_secrets(self, source: str) -> None:
        assert "hardcoded_secret" not in self._kinds(source)

    @pytest.mark.parametrize(
        "value",
        ["COMFY_CLOUD_API_KEY", "NO_REPLY", "HEARTBEAT_OK", "AWS_V2_API_KEY", "TOKEN_123_KEY", "_PRIVATE_KEY"],
    )
    @pytest.mark.parametrize("path", ["config.py", "config.ts", ".env"])
    def test_uppercase_identifiers_are_not_secrets(self, value: str, path: str) -> None:
        assert "hardcoded_secret" not in self._kinds(f'API_KEY = "{value}"\n', path)

    @pytest.mark.parametrize(
        "value",
        ["ABCD_1234_EFGH", "ABCD_1234567890_EFGH", "ABCD_eFGH_12", "ABCD-EFGH-1234"],
    )
    def test_credential_shaped_values_still_fire(self, value: str) -> None:
        assert "hardcoded_secret" in self._kinds(f'API_KEY = "{value}"\n')

    def test_uppercase_password_is_not_exempted(self) -> None:
        assert "hardcoded_password" in self._kinds('password = "SECRET_PHRASE"\n')

    def test_vendor_key_is_not_exempted_by_uppercase_identifier(self) -> None:
        key = "AKIA" + "BCDEFGHIJKLMNOPQ"
        assert "aws_access_key" in self._kinds(f'API_KEY = "{key}"\n')

    def test_vendor_shape_and_keyword_report_one_finding(self) -> None:
        source = 'GITHUB_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyzABCDEFGHIJ"\n'  # pragma: allowlist secret
        assert self._kinds(source) == ["github_token"]

    def test_pem_key_with_escaped_newline_is_flagged(self) -> None:
        source = (
            'PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\\n'
            'MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj\\n"\n'
        )
        assert "private_key_pem" in self._kinds(source, ".env")

    def test_google_key_ending_in_a_dash_is_flagged(self) -> None:
        source = 'MAPS_KEY = "AIzaSyD9K2vQ7xR4mZ1pL8tY6wU3nB0cF5hD9a-"\n'  # pragma: allowlist secret
        assert "google_api_key" in self._kinds(source)

    def test_google_shape_inside_an_integrity_hash_is_ignored(self) -> None:
        run = "AIzaSyD9K2vQ7xR4mZ1pL8tY6wU3nB0cF5hD9aX"  # pragma: allowlist secret
        for integrity in (f"sha512-{run}+Q==", f"sha512-Zm9v/{run}/b2=="):
            source = f'      "integrity": "{integrity}",\n'
            assert "google_api_key" not in self._kinds(source, "package-lock.json")


# Built from parts so no complete token sits in one literal for push protection.
_BODY = "0123456789abcdefghij"
_GLPAT = "glpat" + "-" + _BODY
_AZDO_PAT = "0123456789ABCDEFGHIJ" * 3 + _BODY[:16] + "AZDO" + "0123"


class TestForgeSecretShapes:
    """GitLab and Azure DevOps tokens, and credentials embedded in URLs."""

    @staticmethod
    def _kinds(source: str, path: str = "config.py") -> list[str]:
        return [f["kind"] for f in scan_source(path, source)]

    @pytest.mark.parametrize(
        "token",
        [
            _GLPAT,
            # The routable form: a longer payload, then a version and checksum.
            "glpat" + "-" + _BODY + "_ABCDEFG" + ".01" + "2abcdef",
            "gldt" + "-" + _BODY,
            "glrt" + "-" + _BODY,
            "glptt" + "-" + "0123456789abcdef" * 2 + "01234567",
            "gloas" + "-" + _BODY * 4,
            "glimt" + "-" + _BODY + "ABCDE",
            "glcbt" + "-" + "64" + "_" + _BODY,
            "GR1348941" + _BODY,
        ],
    )
    def test_gitlab_tokens_fire(self, token: str) -> None:
        assert self._kinds(f'client = Gitlab(url, "{token}")\n') == ["gitlab_token"]

    @pytest.mark.parametrize(
        "source",
        [
            'tok = "glpat' + '-0123456789"\n',  # too short
            'tok = "myglpat-' + _BODY + '"\n',  # inside a longer word
            'tok = "glptt-' + _BODY * 2 + '"\n',  # trigger tokens are hex
            'tok = "glpat-' + "xxxx" + _BODY + '"\n',  # placeholder
        ],
    )
    def test_gitlab_lookalikes_do_not_fire(self, source: str) -> None:
        assert "gitlab_token" not in self._kinds(source)

    def test_azure_devops_pat_fires_on_shape_alone(self) -> None:
        assert len(_AZDO_PAT) == 84
        assert self._kinds(f'auth = ("", "{_AZDO_PAT}")\n') == ["azure_devops_pat"]

    def test_azure_devops_signature_must_sit_at_its_offset(self) -> None:
        shifted = "A" + _AZDO_PAT[:-1]
        assert "azure_devops_pat" not in self._kinds(f'auth = ("", "{shifted}")\n')

    def test_legacy_azure_pat_needs_a_key_name(self) -> None:
        legacy = "abcdefghijklmnopqrstuvwxyz234567" + "abcdefghijklmnopqrst"
        assert self._kinds(f'auth = ("", "{legacy}")\n') == []
        assert self._kinds(f'AZURE_DEVOPS_TOKEN = "{legacy}"\n') == ["hardcoded_secret"]

    @pytest.mark.parametrize(
        "url",
        [
            "https://deploy:S3cr3t-Pa55w0rd@gitlab.internal/group/repo.git",  # pragma: allowlist secret
            "https://ci:Zq8vT2mLw9@github.com/owner/repo",  # pragma: allowlist secret
            "https://ci:Zq8vT2mLw9@GitLab.com:443/o/r",  # pragma: allowlist secret
            "git+https://ci:Zq8vT2mLw9@bitbucket.org/o/r",  # pragma: allowlist secret
            "https://org:Zq8vT2mLw9@dev.azure.com/org/p/_git/r",  # pragma: allowlist secret
            "https://org:Zq8vT2mLw9@org.visualstudio.com/p/_git/r",  # pragma: allowlist secret
            "ssh://ci:Zq8vT2mLw9@git.internal:2222/o/r.git",  # pragma: allowlist secret
            "http://ci:Zq8vT2mLw9@10.0.0.5/o/r.git/info/refs",  # pragma: allowlist secret
        ],
    )
    def test_git_remote_credentials_fire(self, url: str) -> None:
        hits = [f for f in scan_source("settings.py", f'DB = "{url}"\n')]
        assert [f["kind"] for f in hits] == ["git_url_credentials"]
        password = url.split("://")[1].split("@")[0].split(":")[1]
        _assert_no_raw(hits[0]["snippet"], password)

    @pytest.mark.parametrize(
        "url",
        [
            "git@github.com:owner/repo.git",
            "ssh://git@github.com/owner/repo.git",
            "ssh://git@gitlab.com:22/owner/repo.git",
            "https://user:password@host.com/repo.git",
            "https://USERNAME:PASSWORD@host.com/repo.git",
            "https://oauth2:${GITLAB_TOKEN}@gitlab.com/o/r.git",
            "https://oauth2:$CI_JOB_TOKEN@gitlab.com/o/r.git",
            "https://user:%AZURE_PAT%@dev.azure.com/org/p/_git/r",
            "https://user:<token>@host.com/repo.git",
            "https://user:********@host.com/repo.git",
            "https://user:your_token_here@host.com/repo.git",
            "postgres://postgres:postgres@localhost:5432/db",
            # Not a git remote: connection strings and plain web URLs.
            "postgres://app:Zq8vT2mLw9@db.internal:5432/app",
            "amqp://svc:k9Rt2xVb7Lp@[::1]:5672/",
            "redis://default:Zq8vT2mLw9@cache.internal:6379",
            "https://ci:Zq8vT2mLw9@api.internal/v1/items",
            "https://ci:Zq8vT2mLw9@github.company.com/o/r",
            # Local and documentation hosts.
            "https://ci:Zq8vT2mLw9@localhost/o/r.git",
            "https://ci:Zq8vT2mLw9@127.0.0.1:3000/o/r.git",
            "https://ci:Zq8vT2mLw9@git.example.com/o/r.git",
            "https://ci:Zq8vT2mLw9@gitea.local/o/r.git",
            # Words, not tokens.
            "https://ci:deploykey@github.com/o/r",
            "https://ci:Sunshine@github.com/o/r",
            "https://ci:abcdefghijkl@github.com/o/r",
            "https://user:pass1234@github.com/o/r.git",
            "https://user:glpat-notreal@gitlab.com/g/p.git",
            # A constant's name stands in for the token in docs.
            "https://USERNAME:PERSONAL_ACCESS_TOKEN@dev.azure.com/org/proj/_git/repo",
            "https://host.com/path:abcdefgh12@other",
        ],
    )
    def test_non_remotes_placeholders_and_ssh_do_not_fire(self, url: str) -> None:
        assert "git_url_credentials" not in self._kinds(f'REMOTE = "{url}"\n')

    def test_token_in_a_url_is_one_finding_named_by_its_vendor(self) -> None:
        source = f'token = "https://oauth2:{_GLPAT}@gitlab.com/o/r.git"\n'
        (hit,) = scan_source("ci.py", source)
        assert hit["kind"] == "gitlab_token"
        _assert_no_raw(hit["snippet"], _GLPAT)

    @pytest.mark.parametrize(
        ("source", "kind"),
        [
            ('password = "https://ci:Xk29fjQ81z@gitlab.com/o/r.git"\n', "hardcoded_password"),  # pragma: allowlist secret
            ('API_KEY = "https://svc:Xk29fjQ81z@github.com/o/r"\n', "hardcoded_secret"),  # pragma: allowlist secret
        ],
    )
    def test_keyword_named_url_credential_keeps_its_kind(self, source: str, kind: str) -> None:
        # Baselines key on kind, so a finding that predates git_url_credentials must not move.
        assert self._kinds(source) == [kind]

    def test_url_credentials_in_prose_fire(self) -> None:
        source = "Clone with `git clone https://ci:Zq8vT2mLw9@git.internal/r.git`.\n"  # pragma: allowlist secret
        assert self._kinds(source, "README.md") == ["git_url_credentials"]


class TestCallKindsIgnoreProseAndText:
    """pickle.loads / os.system / shell=True only count as code, not as text."""

    _KINDS: ClassVar[set[str]] = {"pickle_loads", "os_system", "subprocess_shell_true"}

    def _hits(self, source: str, path: str = "a.py") -> list[dict]:
        return [f for f in scan_source(path, source) if f["kind"] in self._KINDS]

    def test_docstring_mention_does_not_fire_but_the_call_does(self) -> None:
        source = 'def load(x):\n    """Never call pickle.loads on input."""\n    return pickle.loads(x)\n'
        hits = self._hits(source)
        assert [(f["kind"], f["line"]) for f in hits] == [("pickle_loads", 3)]

    def test_comment_and_string_literal_mentions_do_not_fire(self) -> None:
        source = (
            "# avoid os.system here\n"
            'NAME = "os.system"\n'
            'PATTERN = r"subprocess.run(x, shell=True)"\n'
        )
        assert self._hits(source) == []

    def test_markdown_mention_does_not_fire(self) -> None:
        source = "Replaced `pickle.loads` and `os.system` with safer calls.\n"
        assert self._hits(source, "docs/CHANGELOG.md") == []

    def test_secret_in_markdown_is_still_flagged(self) -> None:
        source = 'AWS_ACCESS_KEY_ID = "AKIAQZXNRTVYWMPKLBHG"\n'  # pragma: allowlist secret
        kinds = [f["kind"] for f in scan_source("README.md", source)]
        assert "aws_access_key" in kinds

    def test_real_calls_under_tests_are_low(self) -> None:
        source = "os.system(cmd)\n"
        hits = self._hits(source, "tests/test_x.py")
        assert [f["severity"] for f in hits] == ["low"]
        assert [f["severity"] for f in self._hits(source, "src/x.py")] == ["high"]

    def test_eval_under_tests_is_low(self) -> None:
        findings = scan_source("tests/test_x.py", "eval(expr)\n")
        assert [f["severity"] for f in findings if f["kind"] == "eval_call"] == ["low"]

    def test_multiline_shell_true_in_a_string_does_not_fire(self) -> None:
        source = 'TEMPLATE = """\nsubprocess.run(\n    x,\n    shell=True,\n)\n"""\n'
        assert self._hits(source) == []


class TestSecretPrecision:
    """A secret kind fires on a credential, never on an example of one."""

    _SECRET: ClassVar[set[str]] = {"hardcoded_secret", "hardcoded_password", "public_env_secret"}

    def _kinds(self, source: str, path: str = "src/app.py") -> set[str]:
        return {f["kind"] for f in scan_source(path, source) if f["kind"] in self._SECRET}

    @pytest.mark.parametrize(
        ("path", "source"),
        [
            ("src/app.py", 'emb = get_embedder("openai", api_key="sk-...")\n'),
            ("README.md", 'export ANTHROPIC_API_KEY="sk-ant-…"\n'),
            ("README.md", 'export OPENAI_API_KEY="your-key-here"\n'),
            ("README.md", 'API_KEY="sk-<your key>"\n'),
            ("README.md", 'SECRET="$(op read op://vault/app/secret)"\n'),
            ("deploy.sh", 'TOKEN="$(kubectl get secret app -o jsonpath=\x27{.data.t}\x27 | base64 -d)"\n'),
            ("src/app.py", 'API_KEY = "prefix-${VAR}-suffix"\n'),
            ("src/app.py", 'TOKEN = "prefix-{{var}}-suffix"\n'),
            ("src/app.py", 'TOKEN = "xxxxxxxxxxxx"\n'),
        ],
    )
    def test_elided_or_templated_values_are_not_secrets(self, path: str, source: str) -> None:
        assert self._kinds(source, path) == set()

    @pytest.mark.parametrize(
        "source",
        [
            'client = OpenAI(api_key="ollama")\n',
            'client = OpenAI(api_key="lmstudio")\n',
            '_IGNORE_TOKEN = "repowise-security-ignore"\n',
            'API_KEY_HEADER = "x-api-key"\n',
            '<Bar token="--chart-1" />\n',
        ],
    )
    def test_dummy_values_are_not_secrets(self, source: str) -> None:
        assert self._kinds(source, "src/client.tsx") == set()

    @pytest.mark.parametrize(
        ("path", "source"),
        [
            ("src/Conn.kt", 'const val DISCOVERY_TOKEN = "discoveryToken"\n'),
            ("src/Conn.kt", 'const val KEY_STORE_PASSWORD = "keystorePassword"\n'),
            ("src/tokens.ts", "export const LANGUAGE_SERVER_WS_ADDRESS_TOKEN = 'LANGUAGE_SERVER_WS_ADDRESS_TOKEN';\n"),
            ("src/AuthScheme.kt", 'const val MASKED_PASSWORD = "**************"\n'),
            ("src/Auth.kt", 'val bearerToken = "Bearer ${auth.token.tokenValue}"\n'),
            ("src/auth.ts", 'const token = "Basic ${btoa(user)}";\n'),
            ("README.md", '--spring.datasource.password="DB PASSWORD"\n'),
            ("README.md", 'DB_PASSWORD="YOUR DB PASSWORD"\n'),
        ],
    )
    def test_additional_placeholders_are_not_secrets(self, path: str, source: str) -> None:
        assert self._kinds(source, path) == set()

    @pytest.mark.parametrize(
        ("source", "kind"),
        [
            ('TOKEN = "xQzRmWpLkVnBtYcH"\n', "hardcoded_secret"),
            ('TOKEN = "Bearer eyJhbGciOiJIUzI1NiJ9abc"\n', "hardcoded_secret"),
            ('const val API_TOKEN = "productionAccessKey"\n', "hardcoded_secret"),
            ('password="Tr0ub4dor 3xyz"\n', "hardcoded_password"),
            ('password = "plain$password123"\n', "hardcoded_password"),
        ],
    )
    def test_credential_like_values_still_fire(self, source: str, kind: str) -> None:
        assert self._kinds(source) == {kind}

    @pytest.mark.parametrize(
        "source",
        [
            'TOKEN = "sk-live-9f8a7b6c5d4e"\n',
            'SECRET = "k3y-9f8a-7b6c-5d4e-a1b2"\n',
            'API_KEY = "Ab-cd-Ef-gh-ij-kl"\n',
            'API_KEY = "sk-a****"\n',
            f'API_KEY = "{"q" * 40}"\n',
        ],
    )
    def test_credential_shaped_values_still_fire(self, source: str) -> None:
        assert self._kinds(source) == {"hardcoded_secret"}

    def test_doctest_example_is_not_a_password(self) -> None:
        source = (
            "def mask(line):\n"
            '    """\n'
            "    >>> mask(\"password = 'supersecret99'\")\n"
            '    """\n'
        )
        assert self._kinds(source) == set()

    def test_comment_naming_a_public_env_variable_is_not_a_leak(self) -> None:
        source = "/**\n * Read from NEXT_PUBLIC_REPOWISE_API_KEY on the server.\n */\n"
        assert self._kinds(source, "web/client.ts") == set()

    def test_code_reading_a_public_env_secret_still_fires(self) -> None:
        source = 'const k = process.env.NEXT_PUBLIC_API_KEY; // "NEXT_PUBLIC" is public\n'
        assert self._kinds(source, "web/client.ts") == {"public_env_secret"}

    def test_keyword_secret_after_a_comment_on_the_same_line_is_found_in_code(self) -> None:
        source = 'password = "Tr0ub4dor&3x"  # password = "example"\n'
        assert self._kinds(source) == {"hardcoded_password"}

    def test_vendor_key_in_a_comment_still_fires(self) -> None:
        kinds = {f["kind"] for f in scan_source("src/app.py", "# AKIAQZXNRTVYWMPKLBHG\n")}  # pragma: allowlist secret
        assert kinds == {"aws_access_key"}


class TestNoAlarmOnCodeThatDoesNotShip:
    """Type annotations, release scripts, bundles and marked fixtures raise nothing high."""

    KEY: ClassVar[str] = "AKIA" + "QZXNRTVYWMPKLBHG"

    @staticmethod
    def _found(path: str, source: str) -> list[tuple[str, str]]:
        return [(f["kind"], f["severity"]) for f in scan_source(path, source)]

    @pytest.mark.parametrize("annotation", ["string", "TrustedHTML", "string | TrustedHTML"])
    def test_inner_html_type_annotation_is_not_a_sink(self, annotation: str) -> None:
        source = f"interface Props {{\n  dangerouslySetInnerHTML: {{ __html: {annotation} }};\n}}\n"
        assert self._found("src/props.ts", source) == []

    @pytest.mark.parametrize(
        "source",
        [
            "interface P { __html: string }\n",
            "interface P {\n  __html: string;\n}\n",
            "type P = { __html: string | TrustedHTML };\n",
            "const [h, setH] = useState<{ __html: string }>(init);\n",
            "function f(p: { __html: TrustedHTML }) {}\n",
        ],
    )
    def test_inner_html_in_a_type_position_is_not_a_sink(self, source: str) -> None:
        assert self._found("src/props.tsx", source) == []

    @pytest.mark.parametrize(
        "source",
        [
            "<div dangerouslySetInnerHTML={{__html: string}} />\n",
            "<div dangerouslySetInnerHTML={{ __html: TrustedHTML }} />\n",
            "const props = { __html: string };\n",
            "return {\n  __html: string,\n};\n",
        ],
    )
    def test_inner_html_from_a_value_named_like_a_type_is_still_medium(self, source: str) -> None:
        assert self._found("src/view.tsx", source) == [("unsafe_inner_html", "med")]

    def test_no_written_code_raises_no_symbol_finding(self) -> None:
        sym = SimpleNamespace(name="password", start_line=1)
        source = "declare const password: string;\n"
        for path in ("types/auth.d.ts", "dist/app.min.js"):
            assert scan_source(path, source, [sym]) == []
        kinds = {f["kind"] for f in scan_source("src/auth.ts", source, [sym])}
        assert kinds == {"security_sensitive_symbol"}

    def test_declaration_file_holds_no_sink(self) -> None:
        assert self._found("types/react.d.ts", "type T = { __html: SafeHtml };\n") == []

    def test_inner_html_from_a_value_is_still_medium(self) -> None:
        source = "<div dangerouslySetInnerHTML={{__html: foo}} />\n"
        assert self._found("src/view.tsx", source) == [("unsafe_inner_html", "med")]

    def test_inner_html_from_a_name_starting_with_a_type_word_is_still_medium(self) -> None:
        source = "<div dangerouslySetInnerHTML={{__html: stringValue}} />\n"
        assert self._found("src/view.tsx", source) == [("unsafe_inner_html", "med")]

    def test_a_release_script_sink_is_low(self) -> None:
        source = 'import { execSync } from "child_process";\nexecSync(cmd);\n'
        assert self._found("scripts/release.ts", source) == [("exec_call", "low")]
        assert self._found("src/run.ts", source) == [("exec_call", "high")]

    def test_a_secret_in_a_script_stays_high(self) -> None:
        source = f'import os\nos.system(cmd)\nAWS = "{self.KEY}"\n'
        assert self._found("scripts/deploy.py", source) == [
            ("os_system", "low"),
            ("aws_access_key", "high"),
        ]

    def test_a_minified_bundle_has_no_call_sink_but_keeps_its_secrets(self) -> None:
        bundle = "var a=1;" * 400 + 'var f=new Function("x",y);'
        assert self._found("static/app.3f2a.js", bundle) == []
        assert self._found("static/app.3f2a.js", bundle + f'var k="{self.KEY}";') == [
            ("aws_access_key", "high")
        ]

    def test_new_function_in_written_source_is_still_high(self) -> None:
        source = 'const f = new Function("x", y);\n'
        assert self._found("src/app.js", source) == [("new_function_call", "high")]

    @pytest.mark.parametrize(
        ("path", "marker"),
        [
            ("cfg.go", "// gitleaks:allow"),
            ("README.md", "#gitleaks:allow"),
            ("cfg.go", "// #nosec G101"),
            ("cfg.py", "# nosec"),
            ("cfg.py", "# trufflehog:ignore"),
            ("cfg.py", "# noqa: E501, S105"),
        ],
    )
    def test_other_scanners_allow_markers_silence_a_secret(self, path: str, marker: str) -> None:
        source = f'key = "{self.KEY}"  {marker}\n'
        assert self._found(path, source) == []

    @pytest.mark.parametrize(
        "source",
        [
            'key = "{key}"  # noqa: E501\n',
            'key = "{key} // gitleaks:allow"\n',
            'key = "{key}"\n',
        ],
    )
    def test_a_secret_without_a_marker_comment_still_fires(self, source: str) -> None:
        source = source.format(key=self.KEY)
        assert self._found("src/settings.py", source) == [("aws_access_key", "high")]

    def test_an_allow_marker_does_not_hide_a_call(self) -> None:
        source = "eval(payload)  # nosec\n"
        assert self._found("src/app.py", source) == [("eval_call", "high")]

    def test_a_secret_in_testdata_is_low(self) -> None:
        source = f'var k = "{self.KEY}"\n'
        assert self._found("pkg/testdata/creds.go", source) == [("aws_access_key", "low")]

    def test_slack_prefix_inside_a_document_id_is_not_a_token(self) -> None:
        url = "https://docs.google.com/document/d/1W7KCxOxP-1Fy5EyF2lbJGE2WuKmu5v0suYqoHas1jRM"
        assert self._found("src/links.py", f'URL = "{url}"\n') == []

    def test_kotlin_nested_block_comment_is_comment_to_its_end(self) -> None:
        source = '/* outer /* inner */ password = "Tr0ub4dor&3x" */\nval x = 1\n'
        assert self._found("src/App.kt", source) == []

    def test_go_raw_string_ends_at_a_backslash(self) -> None:
        source = r"s := `C:\dir\`" + '\nt := `password = "Tr0ub4dor&3x"`\n'
        assert self._found("cmd/paths.go", source) == []

    @pytest.mark.parametrize(
        ("options", "masked"),
        [
            ({}, "a " + " " * 12 + " d */ e"),
            ({"nested_comments": True}, "a " + " " * 17 + " e"),
        ],
    )
    def test_masking_nests_block_comments_only_when_asked(
        self, options: dict[str, bool], masked: str
    ) -> None:
        from repowise.core.analysis.security_scan import mask_comments_and_strings

        assert mask_comments_and_strings("a /* b /* c */ d */ e", **options) == masked
