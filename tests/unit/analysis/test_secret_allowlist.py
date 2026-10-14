"""``secret_allowlist``: the paths a repo's own ``.gitleaks.toml`` allowlists."""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.analysis.secret_allowlist import secret_path_allowlist
from repowise.core.analysis.security_scan import scan_source_map

KEY = "AKIA" + "QZXNRTVYWMPKLBHG"


def _config(tmp_path, text: str):
    (tmp_path / ".gitleaks.toml").write_text(text, encoding="utf-8")
    return secret_path_allowlist(tmp_path)


def test_global_allowlist_paths_are_unanchored_searches(tmp_path):
    allowed = _config(
        tmp_path,
        "[allowlist]\npaths = ['''(^|/)cmd/generate/config/rules''', '''testdata''']\n",
    )
    assert allowed("cmd/generate/config/rules/aws.go")
    assert allowed("pkg/testdata/key.txt")
    assert not allowed("detect/detect.go")


def test_allowlists_entries_honour_only_what_a_path_alone_allows(tmp_path):
    allowed = _config(
        tmp_path,
        "[[allowlists]]\npaths = ['''^fixtures/''']\n"
        "[[allowlists]]\ntargetRules = ['aws-access-token']\npaths = ['''^scoped/''']\n"
        "[[allowlists]]\ncondition = 'AND'\nregexes = ['''x''']\npaths = ['''^anded/''']\n"
        "[[allowlists]]\ncondition = 'AND'\npaths = ['''^paths_only/''']\n",
    )
    assert allowed("fixtures/a.go")
    assert allowed("paths_only/a.go")
    assert not allowed("scoped/a.go")
    assert not allowed("anded/a.go")


def test_the_legacy_table_skips_scoped_and_anded_entries_too(tmp_path):
    scoped = "[allowlist]\ntargetRules = ['aws-access-token']\npaths = ['''^a/''']\n"
    anded = "[allowlist]\ncondition = 'AND'\nstopwords = ['x']\npaths = ['''^a/''']\n"
    assert not _config(tmp_path, scoped)("a/key.go")
    assert not _config(tmp_path, anded)("a/key.go")


def test_patterns_match_ascii_only_as_re2_does(tmp_path):
    allowed = _config(tmp_path, "[allowlist]\npaths = ['''^\\w+/key\\.go$''']\n")
    assert allowed("fixtures/key.go")
    assert not allowed("fixturés/key.go")


def test_a_posix_class_pattern_is_skipped(tmp_path):
    # Python reads ``[[:alpha:]]`` as the set ``[:alph`` then a literal ``]``.
    allowed = _config(tmp_path, "[allowlist]\npaths = ['''^[[:alpha:]]+/''', '''^ok/''']\n")
    assert allowed("ok/x.go")
    assert not allowed("a]/x.go")


def test_use_default_brings_gitleaks_default_paths(tmp_path):
    allowed = _config(tmp_path, "[extend]\nuseDefault = true\n")
    assert allowed("config/gitleaks.toml")
    assert not _config(tmp_path, "[extend]\nuseDefault = false\n")("config/gitleaks.toml")


def test_no_config_a_broken_one_or_a_bad_pattern_allowlists_nothing_extra(tmp_path):
    assert not secret_path_allowlist(tmp_path)("a.go")
    assert not secret_path_allowlist(None)("a.go")
    assert not _config(tmp_path, "[allowlist\npaths = 1")("a.go")
    # Python rejects a mid-pattern global flag that RE2 accepts; the rest still apply.
    allowed = _config(tmp_path, "[allowlist]\npaths = ['''^a/(?i)b''', '''^ok/''']\n")
    assert allowed("ok/x.go") and not allowed("a/b.go")


def _parsed(root, rel: str):
    info = SimpleNamespace(path=rel, abs_path=str(root.joinpath(*rel.split("/"))))
    return SimpleNamespace(file_info=info, symbols=[])


def test_the_stored_scan_drops_secrets_in_an_allowlisted_path_only(tmp_path):
    (tmp_path / ".gitleaks.toml").write_text(
        "[allowlist]\npaths = ['''^rules/''']\n", encoding="utf-8"
    )
    source = f'key = "{KEY}"\nos.system(cmd)\n'
    files = {"rules/sample.py": source, "app/deploy.py": source}
    by_file, _ = scan_source_map([_parsed(tmp_path, p) for p in files], files)
    assert [f["kind"] for f in by_file["rules/sample.py"]] == ["os_system"]
    assert {f["kind"] for f in by_file["app/deploy.py"]} == {"aws_access_key", "os_system"}
