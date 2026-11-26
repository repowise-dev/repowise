"""Advisory data kept from OSV input: severity, affected symbols, ranges, fixes, and the stored document."""

from __future__ import annotations

import json

import pytest

from repowise.core.inventory.advisories import (
    Advisory,
    FileAdvisorySource,
    join_usage,
    load_advisories,
    merge_mentions,
    parse_document,
)
from repowise.core.inventory.osv import (
    AffectedImport,
    cvss_base_score,
    dedupe_by_alias,
    extract_affected_imports,
    extract_fixed_version,
    pick_fixed,
    severity_from_osv,
)
from repowise.core.inventory.versions import affected_by, compare_versions, exact_version
from tests.unit.core.inventory_fixture import REPO, session  # noqa: F401  (fixture)

# -- versions ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("eco", "a", "b", "want"),
    [
        ("pypi", "1.0rc1", "1.0", -1),
        ("pypi", "1.0.post1", "1.0", 1),
        ("pypi", "2.10.0", "2.9.0", 1),
        ("npm", "1.0.0-rc.1", "1.0.0", -1),
        ("npm", "1.0.0-alpha.2", "1.0.0-alpha.10", -1),
        ("npm", "1.0.0-alpha", "1.0.0-alpha.1", -1),
        ("npm", "1.0.0+build.5", "1.0.0", 0),
        ("go", "v1.10.0", "v1.9.9", 1),
        ("go", "v0.0.0-20230101000000-abcdef123456", "v0.0.1", -1),
        ("go", "v2.0.0+incompatible", "v2.0.0", 0),
        ("cargo", "0.10.0", "0.9.0", 1),
    ],
)
def test_versions_order_per_ecosystem(eco, a, b, want):
    assert compare_versions(eco, a, b) == want


def test_unknown_orders_give_none():
    assert compare_versions("maven", "1.0", "2.0") is None
    assert compare_versions("npm", "1.0", "1.0.0") is None  # not SemVer
    assert compare_versions("pypi", "not a version", "1.0") is None


@pytest.mark.parametrize(
    ("eco", "spec", "want"),
    [
        ("pypi", "==2.31.0", "2.31.0"),
        ("pypi", "6.0.1", "6.0.1"),
        ("pypi", ">=2.31", None),
        ("pypi", "~=2.31", None),
        ("pypi", "==2.*", None),
        ("pypi", "*", None),
        ("npm", "1.2.3", "1.2.3"),
        ("npm", "=1.2.3", "1.2.3"),
        ("npm", "^1.2.3", None),
        ("npm", "1.x", None),
        ("cargo", "=1.2.3", "1.2.3"),
        ("cargo", "1.2.3", None),  # a bare cargo version is a caret range
        ("go", "v1.2.3", None),  # a go.mod require is a minimum
        ("maven", "1.2.3", None),
        ("pypi", None, None),
    ],
)
def test_exact_version(eco, spec, want):
    assert exact_version(eco, spec) == want


def test_affected_ranges_follow_the_osv_evaluation():
    two_windows = [
        [("introduced", "1.0"), ("fixed", "1.5"), ("introduced", "2.0"), ("fixed", "2.5")]
    ]
    assert affected_by("pypi", "1.2", two_windows) is True
    assert affected_by("pypi", "1.7", two_windows) is False
    assert affected_by("pypi", "2.4", two_windows) is True
    assert affected_by("pypi", "2.5", two_windows) is False
    assert affected_by("pypi", "0.9", two_windows) is False
    since_zero = [[("introduced", "0"), ("last_affected", "3.0.0")]]
    assert affected_by("npm", "3.0.0", since_zero) is True
    assert affected_by("npm", "3.0.1", since_zero) is False
    assert affected_by("go", "v1.4.0", [[("introduced", "0"), ("fixed", "v1.5.0")]]) is True


def test_undecidable_stays_none():
    rng = [[("introduced", "0"), ("fixed", "2.32.0")]]
    assert affected_by("pypi", None, rng) is None
    assert affected_by("maven", "1.0", rng) is None
    assert affected_by("pypi", "2.0", [[("introduced", "0"), ("fixed", "garbage!")]]) is None
    assert affected_by("pypi", "2.0") is None  # neither ranges nor versions
    # The schema requires introduced: a range without one claims nothing.
    assert affected_by("npm", "3.0.0", [[("limit", "2.0.0")]]) is None
    # An explicit versions list decides on its own.
    assert affected_by("pypi", "2.0", versions=["2.0", "2.1"]) is True
    assert affected_by("pypi", "2.2", versions=["2.0", "2.1"]) is False


# -- OSV records ------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "vector", "score"),
    [
        ("CVSS_V3", "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
        ("CVSS_V3", "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N", 6.1),
        ("CVSS_V3", "CVSS:3.0/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:N/A:N", 5.9),
        ("CVSS_V3", "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N", 0.0),
        ("CVSS_V2", "AV:N/AC:L/Au:N/C:P/I:P/A:P", 7.5),
        ("CVSS_V2", "AV:N/AC:M/Au:N/C:N/I:P/A:N", 4.3),
    ],
)
def test_cvss_base_scores(kind, vector, score):
    assert cvss_base_score(kind, vector) == score


def test_severity_precedence_and_labels():
    v3 = {
        "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}]
    }
    assert severity_from_osv(v3) == (9.8, "critical")
    assert severity_from_osv({"database_specific": {"severity": "MODERATE"}}) == (None, "medium")
    assert severity_from_osv({"severity": [{"type": "CVSS_V3", "score": "junk"}]}) == (
        None,
        "unknown",
    )


def test_affected_imports_and_fixed_version_for_a_purl():
    vuln = {
        "affected": [
            {
                "package": {"purl": "pkg:golang/golang.org/x/net"},
                "ecosystem_specific": {
                    "imports": [{"path": "golang.org/x/net/html", "symbols": ["Parse", "Parse"]}]
                },
                "ranges": [
                    {"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "0.9.0"}]},
                    {"type": "SEMVER", "events": [{"introduced": "0.10.0"}, {"fixed": "0.17.0"}]},
                ],
            },
            {
                "package": {"purl": "pkg:golang/other"},
                "ecosystem_specific": {"imports": [{"path": "x"}]},
            },
        ]
    }
    assert extract_affected_imports(vuln, "pkg:golang/golang.org/x/net@v0.8.0") == {
        "paths": ["golang.org/x/net/html"],
        "symbols": ["Parse"],
    }
    assert extract_affected_imports({"affected": [{}]}, "pkg:pypi/x") is None
    # SemVer order, not PEP 440: 0.17.0 is the fix above 0.12.0.
    assert extract_fixed_version(vuln, "pkg:golang/golang.org/x/net@v0.12.0", "v0.12.0") == "0.17.0"
    assert pick_fixed("npm", ["1.2.10", "1.2.9"], "1.2.5") == "1.2.9"
    assert pick_fixed("npm", ["1.2.9"], "1.3.0") is None  # no fix above it
    assert pick_fixed("npm", [], "1.0.0") is None


def test_alias_dedupe_keeps_the_scored_ghsa():
    pysec = {"id": "PYSEC-2018-28", "aliases": ["CVE-2018-18074"]}
    ghsa = {
        "id": "GHSA-x84v",
        "aliases": ["CVE-2018-18074"],
        "severity": [{"type": "CVSS_V3", "score": "x"}],
    }
    other = {"id": "GHSA-none"}
    assert [v["id"] for v in dedupe_by_alias([pysec, other, ghsa])] == ["GHSA-x84v", "GHSA-none"]


_RECORD = {
    "id": "GHSA-9wx4-h78v-vm56",
    "aliases": ["CVE-2024-35195"],
    "summary": "verify=False persists",
    "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:H/I:H/A:N"}],
    "affected": [
        {
            "package": {"ecosystem": "PyPI", "name": "Requests"},
            "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.32.0"}]}],
            "database_specific": {"symbols": ["Session.request"]},
        },
        {
            "package": {"ecosystem": "PyPI", "name": "requests"},
            "ranges": [{"type": "GIT", "repo": "x", "events": [{"introduced": "abc"}]}],
        },
    ],
}


def test_a_record_keeps_severity_symbols_ranges_and_fixes():
    (m,) = parse_document(_RECORD).mentions  # two entries, one package
    (a,) = m.advisories
    assert (a.severity, a.score) == ("medium", 5.6)
    assert a.affected == (AffectedImport("", ("Session.request",)),)
    assert a.affected_symbols == ("Session.request",) and a.affected_paths == ()
    assert a.fixed == ("2.32.0",)
    # The GIT-only entry leaves an empty range: past the fix stays undecided.
    assert a.ranges == ((("introduced", "0"), ("fixed", "2.32.0")), ())
    assert a.affects("pypi", "2.31.0") is True and a.affects("pypi", "2.32.0") is None


def test_a_git_only_entry_keeps_the_advisory_undecided():
    rec = {
        "id": "X-2",
        "affected": [
            {
                "package": {"ecosystem": "PyPI", "name": "pkg"},
                "ranges": [
                    {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "1.0"}]}
                ],
            },
            {
                "package": {"ecosystem": "PyPI", "name": "pkg"},
                "ranges": [{"type": "GIT", "repo": "r", "events": [{"introduced": "abc"}]}],
            },
        ],
    }
    (m,) = parse_document(rec).mentions
    assert m.advisories[0].affects("pypi", "2.0") is None
    assert m.advisories[0].affects("pypi", "0.5") is True


def test_cvss_v2_tops_out_at_high_and_empty_ids_are_refused():
    v2 = {"severity": [{"type": "CVSS_V2", "score": "AV:N/AC:L/Au:N/C:C/I:C/A:C"}]}
    assert severity_from_osv(v2) == (10.0, "high")
    assert parse_document({"id": "", "affected": [{"package": {"name": "x"}}]}).mentions == ()


def test_records_without_symbols_claim_none():
    rec = {"id": "X-1", "affected": [{"package": {"ecosystem": "npm", "name": "lodash"}}]}
    (m,) = parse_document(rec).mentions
    assert m.advisories[0].affected == () and m.advisories[0].affected_symbols == ()


def test_scanner_output_takes_full_records_and_group_scores():
    doc = parse_document(
        {
            "results": [
                {
                    "packages": [
                        {
                            "package": {
                                "name": "requests",
                                "version": "2.31.0",
                                "ecosystem": "PyPI",
                            },
                            "vulnerabilities": [
                                _RECORD,
                                {"id": "PYSEC-2024-1", "aliases": ["CVE-2024-35195"]},
                            ],
                            "groups": [{"ids": ["PYSEC-2024-1"], "max_severity": "7.5"}],
                        }
                    ]
                }
            ]
        }
    )
    (m,) = doc.mentions
    by_id = {a.id: a for a in m.advisories}
    assert by_id["PYSEC-2024-1"].score == 7.5 and by_id["PYSEC-2024-1"].severity == "high"
    assert by_id["GHSA-9wx4-h78v-vm56"].fixed == ("2.32.0",)
    # One CVE, two records: the merge keeps one.
    (pkg,) = merge_mentions(doc.mentions)
    assert len(pkg.advisories) == 1


def test_cyclonedx_dependency_graph_marks_direct_and_transitive():
    doc = parse_document(
        {
            "bomFormat": "CycloneDX",
            "metadata": {"component": {"bom-ref": "app"}},
            "components": [
                {"name": "a", "purl": "pkg:npm/a@1.0.0", "bom-ref": "a"},
                {"name": "b", "purl": "pkg:npm/b@1.0.0", "bom-ref": "b"},
                {"name": "c", "purl": "pkg:npm/c@1.0.0", "bom-ref": "c"},
            ],
            "dependencies": [{"ref": "app", "dependsOn": ["a"]}, {"ref": "a", "dependsOn": ["b"]}],
        }
    )
    assert [(m.name, m.direct) for m in doc.mentions] == [("a", True), ("b", False), ("c", None)]
    # Without the application in the graph, nothing is guessed.
    no_root = parse_document(
        {
            "bomFormat": "CycloneDX",
            "metadata": {"component": {"bom-ref": "app"}},
            "components": [{"name": "a", "purl": "pkg:npm/a@1.0.0", "bom-ref": "a"}],
            "dependencies": [{"ref": "a", "dependsOn": ["b"]}],
        }
    )
    assert [m.direct for m in no_root.mentions] == [None]


def test_the_file_source_hashes_what_it_read(tmp_path):
    f = tmp_path / "osv.json"
    f.write_text(json.dumps(_RECORD), encoding="utf-8")
    import hashlib

    assert FileAdvisorySource().read(str(f)).sha256 == hashlib.sha256(f.read_bytes()).hexdigest()


# -- the join and the stored document ---------------------------------------


def _write(tmp_path, data, name="in.json"):
    f = tmp_path / name
    f.write_text(json.dumps(data), encoding="utf-8")
    return str(f)


async def test_raw_records_are_evaluated_against_exact_pins(session, tmp_path):  # noqa: F811
    # requests is declared ">=2.31" (a range); PyYAML is pinned at 6.0 and 6.0.1.
    yaml_old = {
        "id": "YAML-OLD",
        "affected": [{"package": {"ecosystem": "PyPI", "name": "pyyaml"},
                      "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "5.4"}]}]}],
    }  # fmt: skip
    yaml_new = {
        "id": "YAML-NEW",
        "affected": [{"package": {"ecosystem": "PyPI", "name": "pyyaml"},
                      "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "6.0.1"}, {"fixed": "6.0.2"}]}]}],
    }  # fmt: skip
    result = await join_usage(
        session, REPO, tmp_path, _write(tmp_path, [_RECORD, yaml_old, yaml_new])
    )
    rows = {r["name"].lower(): r for r in result["matched"]}
    (req,) = rows["requests"]["advisories"]
    assert req["affected"] is None and req["basis"] == "declared_range"
    assert req["severity"] == "medium" and req["fixed"] == ["2.32.0"]
    assert req["affected_symbols"] == ["Session.request"]
    yaml = rows["pyyaml"]
    assert [a["id"] for a in yaml["advisories"]] == ["YAML-NEW"]
    assert yaml["advisories"][0]["affected"] is True
    assert yaml["advisories"][0]["version"] == "6.0.1" and yaml["advisories"][0]["fix"] == "6.0.2"
    assert yaml["not_affected"] == [{"id": "YAML-OLD", "version": "6.0"}]


async def test_scanner_matches_stand_and_carry_the_fix(session, tmp_path):  # noqa: F811
    scanner = {"results": [{"packages": [{
        "package": {"name": "requests", "version": "2.31.0", "ecosystem": "PyPI"},
        "vulnerabilities": [_RECORD],
    }]}]}  # fmt: skip
    result = await join_usage(session, REPO, tmp_path, _write(tmp_path, scanner))
    (adv,) = result["matched"][0]["advisories"]
    assert adv["affected"] is True and adv["basis"] == "document" and adv["fix"] == "2.32.0"
    assert result["stored"] is False


async def test_import_stores_the_document_and_replaces_the_last(session, tmp_path):  # noqa: F811
    assert await load_advisories(session, REPO) is None
    scanner = {"results": [{"packages": [
        {"package": {"name": "requests", "version": "2.31.0", "ecosystem": "PyPI"}, "vulnerabilities": [_RECORD]},
        {"package": {"name": "left-pad", "version": "1.3.0", "ecosystem": "npm"}},
    ]}]}  # fmt: skip
    path = _write(tmp_path, scanner)
    result = await join_usage(session, REPO, tmp_path, path, store=True)
    stored = await load_advisories(session, REPO)
    assert stored is not None and stored.sha256 == result["sha256"] and stored.format == "osv"
    assert stored.source == path
    req = stored.get("pypi", "Requests")
    assert req is not None and req.versions == ("2.31.0",)
    (a,) = req.advisories
    assert isinstance(a, Advisory) and a.severity == "medium" and a.fixed == ("2.32.0",)
    assert a.affected == (AffectedImport("", ("Session.request",)),)
    assert a.affects("pypi", "2.31.0") is True
    # Named with no advisory: scanned, nothing found.
    pad = stored.get("npm", "left-pad")
    assert pad is not None and pad.advisories == ()
    assert stored.get("npm", "lodash") is None
    narrowed = await load_advisories(session, REPO, packages=[("npm", "left-pad")])
    assert [p.name for p in narrowed.packages] == ["left-pad"]

    await join_usage(
        session, REPO, tmp_path, _write(tmp_path, [_RECORD], "second.json"), store=True
    )
    again = await load_advisories(session, REPO)
    assert [p.name for p in again.packages] == ["Requests"] and again.sha256 != stored.sha256
    # A document naming nothing leaves the stored one in place.
    empty = await join_usage(
        session, REPO, tmp_path, _write(tmp_path, {"vulns": []}, "e.json"), store=True
    )
    assert empty["stored"] is False
    assert (await load_advisories(session, REPO)).sha256 == again.sha256


def test_records_for_one_cve_collapse_and_keep_every_range():
    a = Advisory("GHSA-a", ("CVE-1",), ranges=((("introduced", "0"), ("fixed", "1.0")),))
    b = Advisory("PYSEC-b", ("CVE-2",))
    c = Advisory("PYSEC-c", ("CVE-1", "CVE-2"), ranges=((("introduced", "2.0"), ("fixed", "2.1")),))
    from repowise.core.inventory.advisories import PackageMention

    (pkg,) = merge_mentions([PackageMention("x", "pypi", None, (a, b, c))])
    (merged,) = pkg.advisories  # linked through c
    assert merged.id == "GHSA-a"
    assert merged.affects("pypi", "2.0.5") is True and merged.affects("pypi", "1.5") is False


def test_unknown_order_gives_a_fix_only_when_there_is_one():
    assert pick_fixed("maven", ["1.9.1", "2.0.5"], "2.1.0") is None
    assert pick_fixed("maven", ["2.0"], "1.5") == "2.0"
