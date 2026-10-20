"""Package usage, the component inventory and the CycloneDX export, over a small index."""

from __future__ import annotations

from repowise.core.inventory.components import component_inventory
from repowise.core.inventory.cyclonedx import cyclonedx_bom, purl
from repowise.core.inventory.usage import package_usage, package_usages, parse_query
from tests.unit.core.inventory_fixture import REPO, session  # noqa: F401  (fixture)


def _by_path(doc):
    return {s["path"]: s for s in doc["sites"]}


async def test_sites_carry_line_component_owners_and_reachability(session, tmp_path):  # noqa: F811
    doc = await package_usage(session, REPO, tmp_path, "requests")
    assert [d["package_key"] for d in doc["declared"]] == ["pypi:requests"]
    sites = _by_path(doc)
    assert list(sites) == [
        "src/app/util.py",
        "src/jobs/worker.py",
        "src/old/legacy.py",
        "tests/test_util.py",
    ]
    util = sites["src/app/util.py"]
    assert util["line"] == 2 and util["statement"] == "from requests import get"
    # requests.compat has no declaration link: it stays inferred, and the
    # declared module alone gives the site its symbols and line.
    assert util["modules"] == [
        {"name": "requests", "mapping": "declared"},
        {"name": "requests.compat", "mapping": "inferred"},
    ]
    assert util["symbols"] == ["get"]
    assert util["component"] == "app"
    assert util["owners"] == ["platform"]
    assert util["mapping"] == "declared"
    assert util["reachability"] == {"state": "reachable", "basis": "import_graph"}

    worker = sites["src/jobs/worker.py"]
    assert worker["modules"] == [{"name": "requests.adapters", "mapping": "declared"}]
    assert worker["line"] == 2  # line 1 is a comment that names requests
    assert worker["owners"] == ["batch-team"]
    # Nothing imports it, but no dead-code finding says so: unknown, not unreachable.
    assert worker["reachability"] == {"state": "unknown", "basis": "no_import_path"}

    legacy = sites["src/old/legacy.py"]
    assert legacy["reachability"] == {"state": "unreachable", "basis": "dead_code"}
    assert legacy["owners"] == []  # an individual CODEOWNERS entry is never named
    assert legacy["owner_counts"] == {"individual": 1}  # but it is counted
    assert "owner_counts" not in util
    assert legacy["component"] == "src"  # the derived top-level directory

    assert sites["tests/test_util.py"]["reachability"]["state"] == "test"
    assert doc["summary"]["reachability"] == {
        "reachable": 1,
        "unreachable": 1,
        "test": 1,
        "unknown": 1,
    }
    assert [c["name"] for c in doc["components"]] == ["app", "jobs", "src", "tests"]


async def test_import_name_alone_is_inferred_and_ecosystem_scoped(session, tmp_path):  # noqa: F811
    yaml, yaml_npm, pyyaml = await package_usages(
        session, REPO, tmp_path, [("yaml", None), ("yaml", "npm"), ("pyyaml", "pypi")]
    )
    assert [(s["path"], s["mapping"]) for s in yaml["sites"]] == [("src/jobs/worker.py", "inferred")]
    assert yaml["sites"][0]["line"] == 3
    assert "No manifest declares" in yaml["note"]
    assert yaml_npm["sites"] == []  # a Python file's import is not an npm package
    # Declared under its distribution name (in two spellings), imported as
    # ``yaml``: no link, said so.
    assert [d["package_key"] for d in pyyaml["declared"]] == ["pypi:PyYAML", "pypi:pyyaml"]
    assert pyyaml["sites"] == []
    assert "import name may differ" in pyyaml["note"]


def test_parse_query():
    assert parse_query("npm:@scope/pkg") == ("@scope/pkg", "npm")
    assert parse_query("requests") == ("requests", None)
    assert parse_query("com.google:guava") == ("com.google:guava", None)


async def test_component_inventory(session, tmp_path):  # noqa: F811
    inv = await component_inventory(session, REPO, tmp_path)
    comps = {c["name"]: c for c in inv["components"]}
    app = comps["app"]
    assert app["owners"]["teams"] == [{"team": "platform", "files": 2}]
    assert app["languages"] == {"python": 2}
    assert app["entry_points"] == ["src/app/main.py"]
    assert app["packages"] == [{"package_key": "pypi:requests", "files": 1}]
    assert comps["jobs"]["owners"]["teams"] == [{"team": "batch-team", "files": 1}]
    assert inv["packages"]["declared_not_imported"] == ["pypi:pytest", "pypi:pyyaml"]
    assert inv["packages"]["declared"] == 3  # PyYAML and pyyaml are one package
    text = repr(inv)
    assert "someone" not in text  # individuals never appear


# Keys the CycloneDX 1.6 JSON schema allows (bom-1.6.schema.json, additionalProperties false).
_OCCURRENCE_KEYS = {"bom-ref", "location", "line", "offset", "symbol", "additionalContext"}
_EVIDENCE_KEYS = {"identity", "occurrences", "callstack", "licenses", "copyright"}
_COMPONENT_TYPES = {
    "application", "framework", "library", "container", "platform", "operating-system",
    "device", "device-driver", "firmware", "file", "machine-learning-model", "data",
    "cryptographic-asset",
}  # fmt: skip
_COMPONENT_KEYS = {
    "type", "mime-type", "bom-ref", "supplier", "manufacturer", "authors", "author",
    "publisher", "group", "name", "version", "description", "scope", "hashes", "licenses",
    "copyright", "cpe", "purl", "omniborId", "swhid", "swid", "modified", "pedigree",
    "externalReferences", "components", "evidence", "releaseNotes", "modelCard", "data",
    "cryptoProperties", "properties", "tags", "signature",
}  # fmt: skip
_TOP_KEYS = {
    "$schema", "bomFormat", "specVersion", "serialNumber", "version", "metadata",
    "components", "services", "externalReferences", "dependencies", "compositions",
    "vulnerabilities", "annotations", "formulation", "declarations", "definitions",
    "properties", "signature",
}  # fmt: skip


async def test_cyclonedx_shape_matches_the_1_6_schema_keys(session, tmp_path):  # noqa: F811
    import re

    bom = await cyclonedx_bom(session, REPO, tmp_path, "demo")
    assert set(bom) <= _TOP_KEYS
    assert bom["bomFormat"] == "CycloneDX" and bom["specVersion"] == "1.6"
    assert re.fullmatch(
        r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        bom["serialNumber"],
    )
    assert set(bom["metadata"]["tools"]) == {"components"}
    refs = [c["bom-ref"] for c in bom["components"]]
    assert len(refs) == len(set(refs))
    for comp in bom["components"]:
        assert set(comp) <= _COMPONENT_KEYS
        assert comp["type"] in _COMPONENT_TYPES and comp["name"]
        assert "version" not in comp and "hashes" not in comp  # not an SBOM
        for prop in comp["properties"]:
            assert set(prop) == {"name", "value"} and isinstance(prop["value"], str)
        if "evidence" in comp:
            assert set(comp["evidence"]) <= _EVIDENCE_KEYS
            for occ in comp["evidence"]["occurrences"]:
                assert set(occ) <= _OCCURRENCE_KEYS and "location" in occ
                assert isinstance(occ.get("line", 0), int) and occ.get("line", 0) >= 0

    by_ref = {c["bom-ref"]: c for c in bom["components"]}
    req = by_ref["pypi:requests"]
    assert req["purl"] == "pkg:pypi/requests"
    occ = {o["location"]: o for o in req["evidence"]["occurrences"]}
    assert occ["src/app/util.py"] == {
        "location": "src/app/util.py",
        "line": 2,
        "symbol": "get",
        "additionalContext": "from requests import get",
    }
    assert {"name": "repowise:declared_version", "value": ">=2.31"} in req["properties"]
    # Only declared modules become occurrences, from the declared statement.
    assert len(req["evidence"]["occurrences"]) == 4
    # Two spellings of one distribution are one component.
    yaml = by_ref["pypi:pyyaml"]
    assert yaml["name"] == "PyYAML" and yaml["purl"] == "pkg:pypi/pyyaml"
    assert [p["value"] for p in yaml["properties"] if p["name"] == "repowise:declared_in"] == [
        "pyproject.toml",
        "requirements.txt",
    ]
    assert "evidence" not in yaml  # declared, no linked import
    assert sum(1 for c in bom["components"] if c["bom-ref"].startswith("pypi:pyyaml")) == 1


def test_purl():
    assert purl("npm", "@scope/pkg") == "pkg:npm/%40scope/pkg"
    assert purl("pypi", "Foo_Bar") == "pkg:pypi/foo-bar"
    assert purl("maven", "com.google.guava:guava") == "pkg:maven/com.google.guava/guava"
    assert purl("go", "github.com/x/y") == "pkg:golang/github.com/x/y"
    assert purl("bazel", "x") is None
    assert purl("maven", "guava") is None  # no group



async def test_only_open_served_dead_code_findings_mark_a_file_unreachable(session, tmp_path):  # noqa: F811
    from sqlalchemy import update

    from repowise.core.persistence.models import DeadCodeFinding

    await session.execute(update(DeadCodeFinding).values(status="acknowledged"))
    await session.commit()
    doc = await package_usage(session, REPO, tmp_path, "requests")
    legacy = _by_path(doc)["src/old/legacy.py"]
    assert legacy["reachability"] == {"state": "unknown", "basis": "no_import_path"}


def test_import_lines_skip_comments_and_read_go_blocks(tmp_path):
    from repowise.core.inventory.usage import _import_lines

    src = tmp_path / "main.go"
    src.write_text(
        'package main\n// import "fmt" in a comment\nimport (\n\t"fmt"\n\t// "os"\n'
        '\t"github.com/x/y"\n)\nvar s = "import fmt"\n',
        encoding="utf-8",
    )
    assert _import_lines(src) == [(4, '"fmt"'), (6, '"github.com/x/y"')]
    js = tmp_path / "a.js"
    js.write_text("// require('lodash')\nconst _ = require('lodash')\n", encoding="utf-8")
    assert _import_lines(js) == [(2, "const _ = require('lodash')")]


async def test_a_resolved_dynamic_load_reaches_a_file(session, tmp_path):  # noqa: F811
    from repowise.core.persistence.models import GraphEdge

    # A "module:attr" string table in an entry point loads the file lazily.
    session.add(
        GraphEdge(
            repository_id=REPO,
            source_node_id="src/app/main.py",
            target_node_id="src/jobs/worker.py",
            edge_type="dynamic_uses",
        )
    )
    await session.commit()
    doc = await package_usage(session, REPO, tmp_path, "requests")
    worker = _by_path(doc)["src/jobs/worker.py"]
    assert worker["reachability"] == {"state": "reachable", "basis": "import_graph"}
