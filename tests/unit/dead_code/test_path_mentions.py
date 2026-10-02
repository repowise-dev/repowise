"""An unreachable file another file names by path is not deletion-ready.

A build script that reads a file by path, or a JSON manifest that lists it,
uses the file without an import edge. The pass caps such a finding to the
review tier, and drops it when a CI workflow, build file, manifest or shell
script names it; it never matches on a bare stem, and a file nobody names
keeps its confidence.
"""

from __future__ import annotations

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.models import DeadCodeFindingData
from repowise.core.analysis.dead_code.name_occurrences import clamp_path_mentions
from repowise.core.analysis.dead_code.risk_factors import RISK_CAP_CONFIDENCE
from tests.unit.dead_code._helpers import _build_graph, _old_date


def _file(path: str, confidence: float = 1.0) -> DeadCodeFindingData:
    return DeadCodeFindingData(
        kind=DeadCodeKind.UNREACHABLE_FILE,
        file_path=path,
        symbol_name=None,
        symbol_kind=None,
        confidence=confidence,
        reason="File has no importers (in_degree=0)",
        last_commit_at=None,
        commit_count_90d=0,
        lines=1,
        evidence=[],
        safe_to_delete=True,
        primary_owner=None,
        age_days=None,
    )


def _clamp(path: str, source: dict[str, str]) -> DeadCodeFindingData:
    finding = _file(path)
    kept = clamp_path_mentions([finding], {p: s.encode() for p, s in source.items()})
    assert kept == [finding], "the finding was dropped"
    return finding


def _dropped(path: str, source: dict[str, str]) -> bool:
    return clamp_path_mentions([_file(path)], {p: s.encode() for p, s in source.items()}) == []


def test_a_json_manifest_listing_example_files_caps_them():
    manifest = '{"examples": ["examples/basic/run.py", "./examples/advanced/run.py"]}'
    source = {"examples.json": manifest, "examples/basic/run.py": "print(1)\n"}
    for path in ("examples/basic/run.py", "examples/advanced/run.py"):
        finding = _clamp(path, source)
        assert finding.confidence == RISK_CAP_CONFIDENCE
        assert finding.safe_to_delete is False
        assert "examples.json" in finding.evidence[-1]


def test_a_build_script_running_a_python_file_by_path_drops_it():
    script = "#!/bin/sh\npython tools/codegen/emit.py > out.txt\n"
    assert _dropped("tools/codegen/emit.py", {"scripts/build.sh": script})


def test_a_ci_workflow_running_a_script_drops_it():
    workflow = "steps:\n  - run: python scripts/emit_sample_dsl.py --check\n"
    assert _dropped("scripts/emit_sample_dsl.py", {".github/workflows/ci.yml": workflow})


def test_dir_and_basename_with_extension_is_enough():
    finding = _clamp("src/pkg/loader.py", {"docs/usage.md": "See `pkg/loader.py`."})
    assert finding.confidence == RISK_CAP_CONFIDENCE


def test_a_build_output_path_names_its_source():
    pkg = '{"main": "src/cli/index.js"}'
    assert _dropped("src/cli/index.ts", {"package.json": pkg})


def test_a_bare_stem_never_matches():
    # "loader" and "loader.py" alone name a file in any directory.
    source = {"README.md": "The loader reads config. loader.py is old.\n"}
    finding = _clamp("src/pkg/loader.py", source)
    assert finding.confidence == 1.0
    assert finding.safe_to_delete is True


def test_a_longer_file_name_sharing_the_prefix_is_not_a_mention():
    finding = _clamp("src/pkg/loader.py", {"a.md": "src/pkg/loader_v2.py"})
    assert finding.confidence == 1.0


def test_a_file_naming_itself_is_not_a_use():
    own = '"""Run as: python scripts/old_migrate.py"""\n'
    finding = _clamp("scripts/old_migrate.py", {"scripts/old_migrate.py": own})
    assert finding.confidence == 1.0


def test_a_truly_dead_file_through_the_analyzer_keeps_full_confidence():
    g = _build_graph(
        nodes={
            "pkg/used.py": {"is_entry_point": True, "symbols": []},
            "pkg/old.py": {"symbols": []},
            "pkg/listed.py": {"symbols": []},
        },
    )
    old = {"commit_count_90d": 0, "last_commit_at": _old_date(400)}
    source = {
        "pkg/used.py": b"print('hi')\n",
        "pkg/old.py": b"x = 1\n",
        "pkg/listed.py": b"y = 2\n",
        "tox.yaml": b"files: [pkg/listed.py]\n",
    }
    report = DeadCodeAnalyzer(
        g,
        git_meta_map={"pkg/old.py": old, "pkg/listed.py": old},
        source_map=source,
    ).analyze(
        {
            "detect_unused_exports": False,
            "detect_unused_internals": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    by_path = {f.file_path: f for f in report.findings if f.kind == DeadCodeKind.UNREACHABLE_FILE}
    # A whole file is review-only, however sure the analyzer is.
    assert by_path["pkg/old.py"].safe_to_delete is False
    assert by_path["pkg/old.py"].confidence == 1.0
    assert by_path["pkg/listed.py"].confidence == RISK_CAP_CONFIDENCE
    assert by_path["pkg/listed.py"].safe_to_delete is False


def _export(path: str, name: str) -> DeadCodeFindingData:
    finding = _file(path)
    finding.kind = DeadCodeKind.UNUSED_EXPORT
    finding.symbol_name = name
    return finding


def test_a_relative_path_resolves_against_the_naming_files_directory():
    config = 'export default { test: { setupFiles: ["./vitest.setup.ts"] } }\n'
    assert _dropped("src/vitest.setup.ts", {"src/vitest.config.ts": config})


def test_a_relative_path_from_a_doc_is_capped_not_dropped():
    finding = _clamp("pkg/run.py", {"pkg/README.md": "Run `./run.py` by hand.\n"})
    assert finding.confidence == RISK_CAP_CONFIDENCE


def test_a_relative_path_never_reaches_a_sibling_directory():
    config = 'export default { test: { setupFiles: ["./vitest.setup.ts"] } }\n'
    finding = _clamp("web/vitest.setup.ts", {"src/vitest.config.ts": config})
    assert finding.confidence == 1.0


def test_the_changesets_changelog_module_and_its_exports_are_used():
    config = '{"changelog": "./changelog-config.js", "commit": false}\n'
    module = ".changeset/changelog-config.js"
    source = {
        ".changeset/config.json": config,
        module: "const getReleaseLine = async () => ''\nconst fns = { getReleaseLine }\n"
        "module.exports = fns\n",
    }
    findings = [_file(module), _export(module, "getReleaseLine")]
    assert clamp_path_mentions(findings, {p: s.encode() for p, s in source.items()}) == []


def test_a_setup_file_a_config_runs_keeps_its_unused_exports():
    # vitest runs a setup file for its side effects; nothing reads its exports.
    config = 'export default { test: { setupFiles: ["./vitest.setup.ts"] } }\n'
    setup = "export function allowNetConnect() {}\nbeforeAll(() => {})\n"
    source = {"src/vitest.config.ts": config, "src/vitest.setup.ts": setup}
    finding = _export("src/vitest.setup.ts", "allowNetConnect")
    assert clamp_path_mentions([finding], {p: s.encode() for p, s in source.items()}) == [finding]


def test_an_export_a_manifest_lists_keeps_its_confidence():
    # A coverage omit list in pyproject.toml names a file without loading it.
    finding = _export("pkg/contrib/plugin.py", "setup")
    source = {"pyproject.toml": b'omit = ["*pkg/contrib/plugin.py"]\n'}
    assert clamp_path_mentions([finding], source) == [finding]


def test_an_export_a_doc_names_by_path_keeps_its_confidence():
    finding = _export("src/lib/api.ts", "helper")
    source = {"docs/api.md": b"See src/lib/api.ts for the client.\n"}
    assert clamp_path_mentions([finding], source) == [finding]
    assert finding.confidence == 1.0
