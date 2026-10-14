"""Non-exported TS/JS top-level symbols must not read as public exports.

``ts_visibility`` only inspects class-member accessibility modifiers, so a
plain top-level ``const helper = ...`` with no ``export`` keyword used to be
stamped ``public`` and surface as an "unused export" dead-code finding (and
inflate the file's derived export list). ``refine_ts_visibility`` demotes
those to ``private`` while keeping every genuine export form public.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


def _symbols(src: str, path: str = "src/mod.ts") -> dict[str, str]:
    language = "javascript" if path.endswith((".js", ".mjs", ".cjs")) else "typescript"
    info = FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language=language,
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    parsed = _PARSER.parse_file(info, src.encode("utf-8"))
    return {s.name: s.visibility for s in parsed.symbols}


def test_unexported_top_level_symbols_are_private() -> None:
    src = """
const skipRetryOn404 = (err: unknown) => false;
function helper(x: number): number { return x; }
const NOOP = () => {};
export const useRepo = () => ({ onErrorRetry: skipRetryOn404, m: NOOP, h: helper });
"""
    vis = _symbols(src)
    assert vis["skipRetryOn404"] == "private"
    assert vis["helper"] == "private"
    assert vis["NOOP"] == "private"
    assert vis["useRepo"] == "public"


def test_inline_export_forms_stay_public() -> None:
    src = """
export function fetchData(): void {}
export const config = { a: 1 };
export default function main(): void {}
export class Client {}
"""
    vis = _symbols(src)
    assert vis["fetchData"] == "public"
    assert vis["config"] == "public"
    assert vis["main"] == "public"
    assert vis["Client"] == "public"


def test_deferred_export_list_stays_public() -> None:
    src = """
const alpha = 1;
const beta = 2;
function gamma(): void {}
export { alpha, gamma as renamed };
"""
    vis = _symbols(src)
    assert vis["alpha"] == "public"
    assert vis["gamma"] == "public"
    assert vis["beta"] == "private"


def test_export_default_identifier_stays_public() -> None:
    src = """
const handler = () => {};
export default handler;
"""
    assert _symbols(src)["handler"] == "public"


def test_class_members_keep_modifier_visibility() -> None:
    src = """
export class Widget {
  render(): void {}
  private hide(): void {}
}
"""
    vis = _symbols(src)
    assert vis["render"] == "public"
    assert vis["hide"] == "private"


def test_commonjs_file_keeps_everything_public() -> None:
    # ``module.exports`` assigns the export surface dynamically; per-name
    # tracking is unreliable, so nothing is demoted.
    src = """
const run = () => {};
const internal = () => {};
module.exports = { run };
"""
    vis = _symbols(src, path="src/mod.js")
    assert vis["run"] == "public"
    assert vis["internal"] == "public"


def test_commonjs_named_export_assignment_keeps_everything_public() -> None:
    src = """
const run = () => {};
const internal = () => {};
exports.run = run;
"""
    vis = _symbols(src, path="src/mod.js")
    assert vis["internal"] == "public"


def test_reading_an_exports_variable_is_not_commonjs() -> None:
    # A VS Code extension's activated ``exports`` object read as a value.
    src = """
interface GitExtension { readonly enabled: boolean; }
export async function getApi(ext: { exports: GitExtension }) {
  const exports = ext.exports;
  if (!exports.enabled) return null;
  return exports["enabled"];
}
"""
    vis = _symbols(src)
    assert vis["GitExtension"] == "private"
    assert vis["getApi"] == "public"


_CJS_WRITES = [
    "module.exports = { run };",
    "module.exports.run = run;",
    'module.exports["run"] = run;',
    'module["exports"] = { run };',
    "module['exports'] = { run };",
    "exports.run = run;",
    'exports["run"] = run;',
    "Object.assign(exports, { run });",
    "Object.assign(module.exports, { run });",
    'Object.defineProperty(exports, "run", { value: run });',
    'Object.defineProperty(module.exports, "run", { value: run });',
]


@pytest.mark.parametrize("write", _CJS_WRITES)
def test_each_commonjs_write_form_keeps_everything_public(write: str) -> None:
    src = f"const run = () => {{}};\nconst internal = () => {{}};\n{write}\n"
    assert _symbols(src, path="src/mod.js")["internal"] == "public"


@pytest.mark.parametrize(
    "read",
    [
        "const prev = module.exports;",
        "if (module.exports === prev) {}",
        "use(module.exports);",
        "const enabled = ext.exports.enabled;",
    ],
)
def test_reading_module_exports_is_not_commonjs(read: str) -> None:
    src = f"const internal = () => {{}};\nexport const run = () => {{}};\n{read}\n"
    vis = _symbols(src, path="src/mod.ts")
    assert vis["internal"] == "private"
    assert vis["run"] == "public"


def test_export_shape_still_reads_a_commonjs_default() -> None:
    from repowise.core.analysis.dead_code.entry_shape import export_shape

    blob = b"const run = () => {};\nmodule.exports = run;\n"
    assert export_shape("src/mod.js", frozenset({"run"}), blob) == frozenset({"default"})
    read_only = b"export const run = 1;\nconst prev = module.exports;\n"
    assert export_shape("src/mod.ts", frozenset({"run"}), read_only) == frozenset({"run"})
