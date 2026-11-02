"""Object-literal methods inside an anonymous callback, end to end.

The parser keeps them as symbols qualified by their owning binding. These
tests pin that the calls inside them resolve with the method as caller, that
SFC projection keeps their lines on the original file, and that dead code
never flags them.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_FILES = {
    "src/checks.ts": "export function gtCheck(v: number, p: number) { return v > p; }\n",
    "src/helper.ts": "export function helper() { return 1; }\n",
    "src/schemas.ts": """\
import * as checks from "./checks";
export const ZodNumber = $constructor("ZodNumber", (inst: any) => {
  install(inst, {
    gt(v: number, p: number) { return checks.gtCheck(v, p); },
  });
});
export const ZodBigInt = $constructor("ZodBigInt", (inst: any) => {
  install(inst, {
    gt(v: number, p: number) { return checks.gtCheck(v, p); },
  });
});
""",
    "src/C.vue": """\
<template>
  <button @click="inc">{{ count }}</button>
</template>

<script>
import { helper } from "./helper";
export const useStore = defineStore("s", {
  actions: {
    inc() { return helper(); },
  },
});
export default {
  data() { return { count: 0 }; },
  methods: {
    inc() { return helper(); },
  },
};
</script>
""",
    "src/Counter.svelte": """\
<script lang="ts">
  import { helper } from "./helper";
  export const store = createStore({
    actions: {
      bump() { return helper(); },
    },
  });
</script>

<button on:click={() => store.actions.bump()}>x</button>
""",
}


def _build(tmp_path: Path):
    for rel, text in _FILES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    builder = GraphBuilder(tmp_path)
    parser = ASTParser()
    source_map: dict[str, bytes] = {}
    for fi in FileTraverser(tmp_path).traverse():
        src = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, src))
        source_map[fi.path] = src
    builder.set_source_map(source_map)
    builder.build()
    graph = builder.graph()
    report = DeadCodeAnalyzer(
        graph, {}, parsed_files=builder._parsed_files, source_map=source_map, repo_root=tmp_path
    ).analyze({"detect_unused_internals": True})
    calls = {(u, v) for u, v, d in graph.edges(data=True) if d.get("edge_type") == "calls"}
    return graph, calls, report


def test_mixin_calls_resolve_from_each_method(tmp_path: Path) -> None:
    graph, calls, _ = _build(tmp_path)
    assert ("src/schemas.ts::ZodNumber::gt", "src/checks.ts::gtCheck") in calls
    assert ("src/schemas.ts::ZodBigInt::gt", "src/checks.ts::gtCheck") in calls
    assert graph.nodes["src/schemas.ts::ZodNumber::gt"]["start_line"] == 4
    assert graph.nodes["src/schemas.ts::ZodBigInt::gt"]["start_line"] == 9


def test_vue_script_keeps_options_ids_and_adds_the_store_action(tmp_path: Path) -> None:
    graph, calls, _ = _build(tmp_path)
    # Options API members were symbols already and keep their ids.
    assert graph.nodes["src/C.vue::inc"]["start_line"] == 15
    assert graph.nodes["src/C.vue::data"]["start_line"] == 13
    # The store action is new, on its .vue line, and does not collide.
    assert graph.nodes["src/C.vue::actions::inc"]["start_line"] == 9
    assert ("src/C.vue::actions::inc", "src/helper.ts::helper") in calls
    assert ("src/C.vue::inc", "src/helper.ts::helper") in calls


def test_svelte_script_method_lands_on_its_line(tmp_path: Path) -> None:
    graph, calls, _ = _build(tmp_path)
    assert graph.nodes["src/Counter.svelte::actions::bump"]["start_line"] == 5
    assert ("src/Counter.svelte::actions::bump", "src/helper.ts::helper") in calls


def test_dead_code_never_flags_the_new_methods(tmp_path: Path) -> None:
    _, _, report = _build(tmp_path)
    flagged = {f.symbol_name for f in report.findings if f.symbol_name}
    assert not flagged & {"gt", "inc", "bump"}
