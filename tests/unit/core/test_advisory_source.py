"""The ``advisory-source`` seam: reading Syft, SPDX, CycloneDX and OSV files, and the join."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.inventory.advisories import (
    FileAdvisorySource,
    advisory_sources,
    from_purl,
    join_usage,
    parse_document,
)
from tests.unit.core.inventory_fixture import REPO, session  # noqa: F401  (fixture)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "advisories"


def _read(name: str):
    return FileAdvisorySource().read(str(FIXTURES / name))


def test_local_file_source_is_the_default():
    assert advisory_sources.default == "file"
    assert isinstance(advisory_sources.get(), FileAdvisorySource)


def test_syft():
    doc = _read("syft.json")
    assert doc.format == "syft"
    assert [(m.ecosystem, m.name, m.version) for m in doc.mentions] == [
        ("pypi", "requests", "2.31.0"),
        ("pypi", "urllib3", "2.0.7"),
        ("npm", "left-pad", "1.3.0"),
    ]


def test_spdx_takes_the_purl_and_keeps_packages_without_one():
    doc = _read("spdx.json")
    assert doc.format == "spdx"
    assert [(m.ecosystem, m.name, m.version) for m in doc.mentions] == [
        ("pypi", "pyyaml", "6.0.1"),
        (None, "requests", "2.31.0"),
    ]


def test_osv_record_and_scanner_output():
    record = _read("osv.json")
    assert record.format == "osv"
    (m,) = record.mentions
    assert (m.ecosystem, m.name, m.version) == ("pypi", "requests", None)
    assert m.advisories[0].id == "GHSA-9wx4-h78v-vm56"
    assert m.advisories[0].aliases == ("CVE-2024-35195",)

    scanner = _read("osv-scanner.json")
    assert [(m.name, m.version, [a.id for a in m.advisories]) for m in scanner.mentions] == [
        ("requests", "2.31.0", ["GHSA-9wx4-h78v-vm56"]),
        ("jinja2", "3.1.2", ["GHSA-h5c8-rqwp-cp95"]),
    ]
    # A list of records and an API ``vulns`` page read the same way.
    data = json.loads((FIXTURES / "osv.json").read_text(encoding="utf-8"))
    assert parse_document([data]).mentions == record.mentions
    assert parse_document({"vulns": [data]}).mentions == record.mentions


def test_cyclonedx_input_reads_nested_components():
    doc = parse_document(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "components": [
                {
                    "type": "library",
                    "name": "a",
                    "purl": "pkg:npm/%40s/a@1.0.0",
                    "components": [{"type": "library", "name": "b", "version": "2"}],
                }
            ],
        }
    )
    assert [(m.ecosystem, m.name, m.version) for m in doc.mentions] == [
        ("npm", "@s/a", "1.0.0"),
        (None, "b", "2"),
    ]


def test_unknown_documents_are_refused(tmp_path):
    with pytest.raises(ValueError):
        parse_document({"hello": "world"})
    bad = tmp_path / "x.json"
    bad.write_text("{nope", encoding="utf-8")
    with pytest.raises(ValueError):
        FileAdvisorySource().read(str(bad))
    with pytest.raises(ValueError, match="cannot be read"):
        FileAdvisorySource().read(str(tmp_path / "missing.json"))
    with pytest.raises(ValueError, match="cannot be read"):
        FileAdvisorySource().read(str(tmp_path))  # a directory
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    with pytest.raises(ValueError, match="nested too deeply"):
        FileAdvisorySource().read(str(deep))


def test_from_purl():
    assert from_purl("pkg:maven/org.apache/commons-text@1.9") == (
        "maven",
        "org.apache:commons-text",
        "1.9",
    )
    assert from_purl("pkg:golang/github.com/x/y@v1.2.0?type=module") == (
        "go",
        "github.com/x/y",
        "v1.2.0",
    )
    assert from_purl("not a purl") == (None, None, None)
    # A raw npm scope keeps its "@"; the version is after the last "@".
    assert from_purl("pkg:npm/@scope/pkg@1.0.0") == ("npm", "@scope/pkg", "1.0.0")
    assert from_purl("pkg:npm/@scope/pkg") == ("npm", "@scope/pkg", None)


async def test_join_by_name_reports_usage_and_unfound_advisories(session, tmp_path):  # noqa: F811
    result = await join_usage(session, REPO, tmp_path, str(FIXTURES / "osv-scanner.json"))
    assert result["format"] == "osv"
    assert result["mentions"] == 2
    (row,) = result["matched"]
    assert row["name"] == "requests" and row["declared"] is True
    assert [a["id"] for a in row["advisories"]] == ["GHSA-9wx4-h78v-vm56"]
    assert row["summary"]["reachability"]["reachable"] == 1
    assert {s["path"] for s in row["sites"]} >= {"src/app/util.py"}
    assert result["not_found"] == 1
    assert [r["name"] for r in result["not_found_with_advisories"]] == ["jinja2"]
    assert "not evaluated" in result["method"]


async def test_join_passes_declared_packages_to_a_plugged_source(session, tmp_path):  # noqa: F811
    from repowise.core.inventory.advisories import PackageMention, SourceDocument

    seen = {}

    class Feed:
        def read(self, location, *, packages=()):
            seen["packages"] = list(packages)
            return SourceDocument("osv", (PackageMention("requests", "pypi"),))

    advisory_sources.register("test-feed", Feed())
    try:
        result = await join_usage(session, REPO, tmp_path, "ignored", source="test-feed")
    finally:
        advisory_sources._impls.pop("test-feed")
    assert ("pypi", "requests") in seen["packages"]
    assert [r["name"] for r in result["matched"]] == ["requests"]
