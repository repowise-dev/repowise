"""The name-only call tiers stay inside the caller's build.

Ktor's samples are independent Gradle builds side by side: a bare call in one
cannot mean a top-level function of another, however unique its name. A build
that composes another in (``includeBuild``, ``buildSrc``) sees it, and a Gradle
build script sees only the build logic its build composes in, never the
sources it builds.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.build_roots import BuildRoots
from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import parse_file
from repowise.core.ingestion.resolvers.jvm_gradle import included_builds

_LANGUAGES = {"kt": "kotlin", "kts": "kotlin", "dart": "dart"}


def _write(tmp_path: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _parse(tmp_path: Path, files: dict[str, str]) -> dict[str, ParsedFile]:
    _write(tmp_path, files)
    out: dict[str, ParsedFile] = {}
    for rel, content in files.items():
        language = _LANGUAGES.get(rel.rpartition(".")[2])
        if language is None:
            continue
        info = FileInfo(
            path=rel,
            abs_path=str(tmp_path / rel),
            language=language,
            size_bytes=len(content),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        out[rel] = parse_file(info, content.encode("utf-8"))
    return out


def _callees(
    tmp_path: Path, files: dict[str, str], caller: str, imports: dict[str, str] | None = None
) -> set[str]:
    """Callees of *caller*; *imports* maps a file to the one file it imports."""
    parsed = _parse(tmp_path, files)
    targets: dict[str, set[str]] = {p: set() for p in parsed}
    for importer, imported in (imports or {}).items():
        targets[importer].add(imported)
        for imp in parsed[importer].imports:
            imp.resolved_file = imported
    resolver = CallResolver(parsed, targets, repo_path=str(tmp_path))
    path = caller.partition("::")[0]
    return {
        rc.callee_id
        for rc in resolver.resolve_file(path, parsed[path].calls)
        if rc.caller_id == caller
    }


HELPER = "package b\n\nfun registerRoutes(x: Int) = x\n"
CALLER = "package a\n\nfun main() {\n    registerRoutes(1)\n}\n"


class TestBuildRoots:
    def test_roots_are_settings_and_lone_builds(self, tmp_path: Path) -> None:
        files = {
            "settings.gradle.kts": "",
            "app/build.gradle.kts": "",
            "app/src/A.kt": "",
            "logic/settings.gradle": "",
            "logic/src/L.kt": "",
            "buildSrc/src/B.kt": "",
            "pkg/pubspec.yaml": "name: pkg\n",
            "pkg/lib/p.dart": "",
        }
        _write(tmp_path, files)
        roots = BuildRoots(str(tmp_path), files)
        assert roots.root_of("app/src/A.kt") == ""
        assert roots.root_of("logic/src/L.kt") == "logic"
        assert roots.root_of("buildSrc/src/B.kt") == "buildSrc"
        assert roots.root_of("pkg/lib/p.dart") == "pkg"
        assert roots.composes("", "buildSrc")
        assert not roots.composes("", "logic")

    def test_a_build_without_settings_is_its_own(self, tmp_path: Path) -> None:
        files = {"one/build.gradle.kts": "", "one/sub/build.gradle.kts": "", "one/sub/X.kt": ""}
        _write(tmp_path, files)
        assert BuildRoots(str(tmp_path), files).root_of("one/sub/X.kt") == "one"

    def test_included_builds_read_both_dialects(self) -> None:
        text = 'pluginManagement {\n  includeBuild("build-logic")\n}\nincludeBuild \'../lib\'\n'
        assert included_builds(text) == ["build-logic", "../lib"]


class TestNameTiersStayInTheBuild:
    def test_a_sibling_build_does_not_answer(self, tmp_path: Path) -> None:
        files = {
            "a/build.gradle.kts": "",
            "a/src/Main.kt": CALLER,
            "b/build.gradle.kts": "",
            "b/src/Helper.kt": HELPER,
        }
        assert _callees(tmp_path, files, "a/src/Main.kt::main") == set()

    def test_one_build_still_answers(self, tmp_path: Path) -> None:
        files = {
            "settings.gradle.kts": 'include(":a", ":b")\n',
            "a/build.gradle.kts": "",
            "a/src/Main.kt": CALLER,
            "b/build.gradle.kts": "",
            "b/src/Helper.kt": HELPER,
        }
        assert _callees(tmp_path, files, "a/src/Main.kt::main") == {
            "b/src/Helper.kt::registerRoutes"
        }

    def test_an_included_build_answers(self, tmp_path: Path) -> None:
        files = {
            "a/settings.gradle.kts": 'includeBuild("../b")\n',
            "a/src/Main.kt": CALLER,
            "b/settings.gradle.kts": "",
            "b/src/Helper.kt": HELPER,
        }
        assert _callees(tmp_path, files, "a/src/Main.kt::main") == {
            "b/src/Helper.kt::registerRoutes"
        }


SCRIPT_FILES = {
    "settings.gradle.kts": 'includeBuild("build-logic")\n',
    "app/build.gradle.kts": "plugins {\n}\napi(1)\nconfigureFlavors(2)\n",
    "app/src/Api.kt": "package app\n\nfun api(x: Int) = x\n",
    "build-logic/settings.gradle.kts": "",
    "build-logic/src/Flavors.kt": "fun configureFlavors(x: Int) = x\n",
}


class TestBuildScripts:
    def test_a_script_reaches_its_build_logic_only(self, tmp_path: Path) -> None:
        callees = _callees(tmp_path, SCRIPT_FILES, "app/build.gradle.kts::__module__")
        assert callees == {"build-logic/src/Flavors.kt::configureFlavors"}

    def test_a_precompiled_script_plugin_sees_its_own_build(self, tmp_path: Path) -> None:
        script = "buildSrc/src/main/kotlin/my-conv.gradle.kts"
        files = {
            "settings.gradle.kts": "",
            "buildSrc/src/main/kotlin/Utils.kt": "fun configureKotlin() {}\n",
            script: "configureKotlin()\n",
        }
        assert _callees(tmp_path, files, f"{script}::__module__") == {
            "buildSrc/src/main/kotlin/Utils.kt::configureKotlin"
        }


class TestReexports:
    def test_a_package_re_exported_by_an_import_is_reachable(self, tmp_path: Path) -> None:
        files = {
            "a/pubspec.yaml": "name: a\n",
            "b/pubspec.yaml": "name: b\n",
            "c/pubspec.yaml": "name: c\n",
            "a/lib/a.dart": "import 'package:b/b.dart';\nvoid useA() { fromC(); }\n",
            "b/lib/b.dart": "export 'package:c/c.dart';\nvoid fromB() {}\n",
            "c/lib/c.dart": "void fromC() {}\n",
        }
        imports = {"a/lib/a.dart": "b/lib/b.dart", "b/lib/b.dart": "c/lib/c.dart"}
        callees = _callees(tmp_path, files, "a/lib/a.dart::useA", imports)
        assert callees == {"c/lib/c.dart::fromC"}
