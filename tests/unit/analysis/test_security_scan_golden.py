"""Golden corpus for ``scan_source``: one small source per pattern kind.

Each kind has Python and JS variants where the language allows it, one where
the match sits in a comment or string, and the low-severity, prose, origin and
allowlist paths. Every finding field and the order they come back in is
pinned, so a refactor of the scanner cannot move a finding unseen. Rewrite with
``REPOWISE_REWRITE_GOLDEN=1`` only for an intended behaviour change.

Vendor-shaped keys are assembled by concatenation so the source never holds a
literal that a push-protection scanner would stop; the golden stores only the
masked snippets.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text

from repowise.core.analysis.security_scan import (
    SecurityScanner,
    masked_snippet,
    scan_source,
    scan_source_map,
)
from tests.unit.analysis.test_security_scan import _fresh_session_factory

GOLDEN = Path(__file__).parent / "golden" / "security_scan_corpus.json"

_AWS = "AKIA" + "QZXNRTVYWMPKLBHG"
_GH = "ghp_" + "Kq7mZ2xV9pL4rT8wN3bY6cH1jF5dS0gA2eUo"
_SLACK = "xoxb" + "-4721609583-T5Rk9mPz2Qa"
_GOOGLE = "AIza" + "SyD9K2vQ7xR4mZ1pL8tY6wU3nB0cF5hD9aX"
_STRIPE = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc"
_GLPAT = "glpat" + "-" + "Zq8RkT2mWx9pL4vN7bY3"
_AZDO = "0123456789ABCDEFGHIJ" * 3 + "Zq8RkT2mWx9pL4vN" + "AZDO" + "0123"
_PEM_BODY = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj"
_PEM_HEAD = "-----BEGIN RSA " + "PRIVATE KEY-----"
_PEM_TAIL = "-----END RSA " + "PRIVATE KEY-----"
_GIT_URL = "https://deploy:" + "Xk9!mQ2vLp7z" + "@github.com/acme/app.git"


def _sym(name: str, line: int) -> SimpleNamespace:
    return SimpleNamespace(name=name, qualified_name=name, start_line=line)


# name -> (path, source, symbols)
CASES: dict[str, tuple[str, str, list]] = {
    # -- eval / exec ---------------------------------------------------------
    "eval_py": ("app/run.py", "x = eval(user_input)\nobj.eval(y)\n", []),
    "eval_py_comment": ("app/run.py", "# eval(user_input) is unsafe\ns = 'eval(x)'\n", []),
    "eval_py_unparsable": ("app/run.py", "x = eval(data\nexec(code) # eval(z)\n", []),
    "exec_py": ("app/run.py", "exec(code)\n", []),
    "eval_js": ("web/run.js", "const v = eval(input);\n// eval(x)\nconst s = 'eval(y)';\n", []),
    "exec_js_regex": ("web/re.js", "const m = /a/.exec(s);\nre.exec(line);\n", []),
    "exec_js_namespace": (
        "web/sh.js",
        'const cp = require("child_process");\ncp.exec(cmd);\ncp.execFile("ls");\n',
        [],
    ),
    "exec_js_named_import": (
        "web/sh.ts",
        'import { exec as run, spawn } from "node:child_process";\nrun(cmd);\nspawn("ls");\n',
        [],
    ),
    "exec_js_require_call": ("web/sh.js", 'require("child_process").execSync("ls");\n', []),
    # -- dangerous calls -----------------------------------------------------
    "pickle_py": (
        "app/store.py",
        "data = pickle.loads(blob)\ncopy = pickle.loads(pickle.dumps(x))\n# pickle.loads(b)\n",
        [],
    ),
    "subprocess_shell_py": (
        "app/sh.py",
        "subprocess.run(cmd, shell=True)\n"
        "subprocess.Popen(\n    cmd,\n    shell=True,\n)\n"
        "doc = 'subprocess.run(x, shell=True)'\n",
        [],
    ),
    "os_system_py": ("app/sh.py", "os.system(cmd)\n# os.system(cmd)\n", []),
    # -- keyword secrets -----------------------------------------------------
    "password_py": (
        "app/conf.py",
        'DB_PASSWORD = "Tr0ub4dor&3xyz"\n'
        'password = "changeme"\n'
        'password = "<your password>"\n'
        '# password = "Tr0ub4dor&3xyz"\n'
        'password = "short"\n',
        [],
    ),
    "password_js": ("web/conf.js", 'const password = "Tr0ub4dor&3xyz";\n', []),
    "secret_py": (
        "app/conf.py",
        'API_KEY = "q8Rk2mWx9pL4vN7b"\n'
        'secret = "x-api-key-header"\n'
        'token = "{{ vault_token }}"\n'
        'api_key = "lmstudio"\n'
        'SECRET = "SECRET"\n'
        'access_key = "--flag-value"\n',
        [],
    ),
    # -- vendor key shapes ---------------------------------------------------
    "aws_py": ("app/aws.py", f'client_id = "{_AWS}"\n# {_AWS}\n', []),
    "aws_test_path": ("tests/fixtures/keys.py", f'KEY = "{_AWS}"\n', []),
    "github_js": ("web/gh.js", f'const t = "{_GH}";\n', []),
    "slack_py": ("app/slack.py", f'SLACK = "{_SLACK}"\n', []),
    "google_js": ("web/maps.js", f'const k = "{_GOOGLE}";\n', []),
    "stripe_py": ("app/pay.py", f'stripe.api_key = "{_STRIPE}"\n', []),
    "gitlab_py": ("app/gl.py", f'GL = "{_GLPAT}"\n', []),
    "azure_py": ("app/az.py", f'auth = ("", "{_AZDO}")\n', []),
    "git_url_py": (
        "app/remote.py",
        f'REMOTE = "{_GIT_URL}"\n'
        'LOCAL = "https://u:Xk9#mQ2vLp7z@localhost/r.git"\n'
        'TEMPLATE = "https://u:${TOKEN}@github.com/o/r.git"\n',
        [],
    ),
    "pem_multiline": (
        "deploy/key.py",
        f'KEY = """{_PEM_HEAD}\n{_PEM_BODY}\n{_PEM_BODY}\n{_PEM_TAIL}"""\nos.system(KEY)\n',
        [],
    ),
    "pem_inline_json": (
        "deploy/creds.json",
        '{"private_key": "' + _PEM_HEAD + "\\n" + _PEM_BODY + "\\n" + _PEM_TAIL + '"}\n',
        [],
    ),
    "pem_header_only": ("app/pem.py", f'HEADER = "{_PEM_HEAD}"\n', []),
    # -- SQL -----------------------------------------------------------------
    "fstring_sql_py": (
        "app/db.py",
        'q = f"SELECT * FROM users WHERE id = {uid}"\n'
        'q = f"SELECT {col} FROM users"\n'
        'q = f"SELECT * FROM t "\n    "WHERE name LIKE \'%{n}%\'"\n'
        'q = f"""\nSELECT *\nFROM t\nWHERE a = {a}\n"""\n'
        "s = 'f\"SELECT * FROM t WHERE id = {x}\"'\n",
        [],
    ),
    "concat_sql_py": (
        "app/db.py",
        'cur.execute("SELECT * FROM t WHERE id=" + uid)\n',
        [],
    ),
    "template_sql_js": (
        "web/db.ts",
        "const q = `SELECT * FROM users WHERE id = ${id}`;\n"
        "const msg = `Order update failed: ${status}`;\n"
        "const u = `UPDATE t SET a = ${a}`;\n",
        [],
    ),
    "template_sql_py": ("app/db.py", "q = `SELECT * FROM t WHERE a = ${a}`\n", []),
    # -- TLS -----------------------------------------------------------------
    "tls_requests_py": (
        "app/http.py",
        "import requests\n"
        "s = requests.Session()\n"
        "requests.get(url, verify=False)\n"
        "s.post(url, verify=False)\n"
        "s.verify = False\n"
        "check(x, verify=False)\n",
        [],
    ),
    "tls_no_client_py": ("app/check.py", "run(x, verify=False)\n", []),
    "tls_unparsable_py": ("app/check.py", "requests.get(url, verify=False\n", []),
    "tls_js": (
        "web/agent.js",
        "const a = new https.Agent({ rejectUnauthorized: false });\n"
        "// rejectUnauthorized: false\n",
        [],
    ),
    # -- weak hash -----------------------------------------------------------
    "weak_hash_py": (
        "app/hash.py",
        "h = hashlib.md5(data)\n"
        "c = hashlib.sha1(data, usedforsecurity=False)\n"
        "sha1 = commit.sha1\n"
        "# md5(x)\n"
        "mac = hmac.new(k, m, digestmod='sha1')\n",
        [],
    ),
    "weak_hash_js": (
        "web/hash.js",
        'const h = crypto.createHash("md5");\nconst s = "md5(x)";\n',
        [],
    ),
    # -- JS only -------------------------------------------------------------
    "inner_html_jsx": (
        "web/View.tsx",
        "<div dangerouslySetInnerHTML={{__html: html}} />\n"
        "type P = { __html: string };\n"
        "const x = {__html: \"<b>static</b>\"};\n",
        [],
    ),
    "inner_html_py": ("app/view.py", "x = {'__html': html}\nd = dict(__html= html)\n", []),
    "public_env_js": (
        "web/env.ts",
        "const k = process.env.NEXT_PUBLIC_API_KEY;\n"
        "// NEXT_PUBLIC_SECRET_TOKEN is read below\n"
        "const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;\n",
        [],
    ),
    "public_env_dotenv": (
        "web/.env.production",
        "NEXT_PUBLIC_STRIPE_SECRET=" + _STRIPE + "\nVITE_API_TOKEN=\n",
        [],
    ),
    "new_function_js": (
        "web/fn.js",
        'const f = new Function("a", body);\n// new Function(x)\n',
        [],
    ),
    "new_function_py": ("app/fn.py", "f = new Function(x)\n", []),
    # -- path, origin and prose ----------------------------------------------
    "prose_readme": (
        "README.md",
        f"Run `os.system(cmd)` then set `KEY={_AWS}`.\neval(x)\n",
        [],
    ),
    "declaration_dts": (
        "web/types.d.ts",
        f'declare const eval: any;\nexport const k = "{_GOOGLE}";\n',
        [_sym("authToken", 1)],
    ),
    "vendored_js": ("vendor/lib.min.js", f'eval(x);var k="{_GOOGLE}";\n', []),
    "tooling_script": ("scripts/release.py", f'os.system(cmd)\nKEY = "{_AWS}"\n', []),
    "test_path_sink": ("tests/test_app.py", "os.system(cmd)\nx = eval(y)\n", []),
    # -- dialects ------------------------------------------------------------
    "go_raw_backtick": (
        "svc/main.go",
        # The raw string ends in a backslash, which escapes nothing in Go.
        "p := `C:\\`; h := md5.Sum(b)\nexec.Command(\"sh\").Run() // md5.Sum(x)\n",
        [],
    ),
    "rust_nested_comment": (
        "svc/main.rs",
        "/* outer /* os.system(x) */ still comment */\nlet s = md5::compute(b);\n",
        [],
    ),
    # -- allowlist markers ---------------------------------------------------
    "allowlist_markers": (
        "app/keys.py",
        f'A = "{_AWS}"  # pragma: allowlist secret\n'
        "# pragma: allowlist nextline secret\n"
        f'B = "{_AWS}"\n'
        f'C = "{_AWS} # nosec"\n'
        f'D = "{_AWS}"  # noqa: S105\n'
        f'E = "{_AWS}"  // gitleaks:allow\n'
        "os.system(cmd)  # nosec\n",
        [],
    ),
    "allowlist_prose": ("docs/keys.md", f"key {_AWS} gitleaks:allow\nkey {_GH}\n", []),
    # -- symbols -------------------------------------------------------------
    "symbols": (
        "app/auth.py",
        "def auth_user(): pass\n",
        [
            _sym("auth_user", 1),
            _sym("hash_password", 4),
            _sym("session", 7),
            _sym("Password", 8),
            _sym("jwt.verify", 9),
            # Fallback to the qualified name, and a missing line read as 0.
            SimpleNamespace(name="", qualified_name="app.auth", start_line=None),
        ],
    ),
    "clean": ("app/math.py", "def add(a, b):\n    return a + b\n", []),
    "crlf_and_formfeed": ("app/run.py", "x = 1\r\n\fy = 2\r\nos.system(c)\r\n", []),
    "long_snippet": (
        "app/long.py",
        "password = '" + "Zq8RkT2mWx9pL4vN" * 8 + "'  " + "x" * 40 + "\n",
        [],
    ),
}


def _scan_all() -> dict[str, dict]:
    return {
        name: {"path": path, "findings": scan_source(path, source, symbols)}
        for name, (path, source, symbols) in CASES.items()
    }


def _source_map_result() -> dict:
    """``scan_source_map``: bytes and str sources, a missing path, the ignore marker."""
    files = {
        "app/a.py": b"os.system(cmd)\nx = eval(y)  # repowise-security-ignore: eval_call\n",
        "app/b.py": "subprocess.run(c, shell=True)  # repowise-security-ignore\n",
        "app/c.py": "os.system(c)  # repowise-security-ignore:\n",
    }
    parsed = [
        SimpleNamespace(file_info=SimpleNamespace(path=p), symbols=[])
        for p in [*files, "app/missing.py"]
    ]
    parsed[-1].symbols = [_sym("check_auth", 3)]
    findings, scanned = scan_source_map(parsed, files)
    return {"findings": findings, "scanned": scanned}


def _persisted_rows(tmp_path: Path) -> dict:
    """``persist`` and ``replace_findings`` against a real SQLite store."""
    from repowise.core.persistence import get_session

    findings = scan_source(*CASES["pickle_py"][:2])

    async def _run() -> dict:
        engine, sf = await _fresh_session_factory(tmp_path)
        async with get_session(sf) as session:
            scanner = SecurityScanner(session, "repo-1")
            first = await scanner.persist("app/store.py", findings, commit_sha="abc123")
            again = await scanner.persist("app/store.py", findings, commit_sha="abc123")
            # A row missing its kind is skipped, not fatal to the batch.
            partial = await scanner.persist("app/x.py", [{"severity": "low"}, *findings])
            empty = await scanner.persist("app/x.py", [])
            await scanner.replace_findings({"app/x.py": findings[:1]}, ["app/x.py"])
        async with get_session(sf) as session:
            res = await session.execute(
                text(
                    "SELECT file_path, kind, severity, snippet, line_number, commit_sha "
                    "FROM security_findings ORDER BY file_path, commit_sha, line_number, kind"
                )
            )
            rows = [list(r) for r in res.fetchall()]
        await engine.dispose()
        return {
            "inserted": [first, again, partial, empty],
            "rows": rows,
        }

    return asyncio.run(_run())


def _actual(tmp_path: Path) -> dict:
    return {
        "scan_source": _scan_all(),
        "scan_source_map": _source_map_result(),
        "masked_snippet": [
            masked_snippet(f'k = "{_AWS}" and {_AWS[:6]}', [(5, 9)]),
            masked_snippet("    " + _PEM_BODY),
        ],
        "persisted": _persisted_rows(tmp_path),
    }


def test_security_scan_matches_golden(tmp_path: Path) -> None:
    actual = _actual(tmp_path)
    if os.environ.get("REPOWISE_REWRITE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert actual == expected


def test_security_scan_is_stable() -> None:
    assert _scan_all() == _scan_all()
