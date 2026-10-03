// Local benchmark for the map at 180x50: the layout (redone only on a resize
// or a style change) and one lit frame with its encoding (a turn's searches,
// reads, the edit and its importers, mid-ripple, and a selection). Three
// scales: a 100-file repo (synthetic), Django's 2,347 recorded files, and a
// 10,000-file repo (synthetic; the server sends its 4,000 largest), then the
// last two zoomed into one folder. Budget 16 ms each; vitest
// guards a frame at 5x that (test/overlay.test.ts).
//
//   npm run bench:map -w @repowise-dev/claude-mod

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const BUDGET_MS = 16;
const RUNS = 50;
const SERVER_CAP = 4_000;
const pkg = resolve(dirname(fileURLToPath(import.meta.url)), "..");

// test/setup gives Node the runtime's Uint8Array.prototype.toBase64.
const bundled = await build({
  stdin: {
    contents: 'import "./test/setup"; export * from "./src/views/map"; export * from "./src/views/overlay"; export { syntheticTree } from "./test/synthetic";',
    resolveDir: pkg,
    loader: "ts",
  },
  bundle: true,
  format: "esm",
  platform: "node",
  mainFields: ["module", "main"],
  write: false,
  logLevel: "warning",
});
const map = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`);

const fixture = (name) => JSON.parse(readFileSync(resolve(pkg, "test/fixtures", name), "utf8"));

function time(label, work) {
  for (let i = 0; i < 5; i++) work(i);
  const samples = [];
  for (let i = 0; i < RUNS; i++) {
    const start = performance.now();
    work(i);
    samples.push(performance.now() - start);
  }
  samples.sort((a, b) => a - b);
  const median = samples[Math.floor(RUNS / 2)];
  const p95 = samples[Math.floor(RUNS * 0.95)];
  console.log(`  ${label.padEnd(30)} median ${median.toFixed(2)} ms  p95 ${p95.toFixed(2)} ms`);
  return p95;
}

/** A turn over these files: matches, two reads, the edit, and importers, all real paths of the feed. */
function turnOver(files, importers) {
  const paths = files.map((f) => f.file_path);
  const edit = paths[Math.floor(paths.length / 3)];
  return { hits: paths.slice(10, 40), reads: [paths[5], edit], named: [paths[5]], importers: importers ?? paths.slice(50, 62), edits: [edit], edit, current: edit };
}

function bench(label, files, importers, root = null) {
  const canvas = { columns: 180, rows: 50, caseInsensitive: true, root };
  const layout = map.layoutMap(files, canvas);
  const lit = turnOver(files, importers);
  const where = root === null ? "" : `, zoomed into ${root}`;
  console.log(`${label}${where}: ${layout.files.length} files, ${layout.drawn.length} with a pixel, ${layout.dense ? "drawn as folders" : "a tile each"}`);
  const layoutP95 = time("layout (on resize or zoom)", () => map.layoutMap(files, canvas));
  const frameP95 = time("lit frame mid-ripple + encode", (i) =>
    map.frameCells(layout, map.resolveLit(layout, lit, lit.edit), { kind: "ripple", t: (i % 30) / 30 }),
  );
  return Math.max(layoutP95, frameP95);
}

const django = fixture("django-health-map.json").files;
const djangoImporters = fixture("django-blast-radius-query-depth1.json").transitive_affected.map((t) => t.path);
const worst = Math.max(
  bench("100 files (synthetic)", map.syntheticTree(100).files),
  bench("Django (recorded)", django, djangoImporters),
  bench("10,000 files (synthetic, the server's 4,000 largest)", map.syntheticTree(10_000).files.slice(0, SERVER_CAP)),
  bench("Django (recorded)", django, djangoImporters, "django/db/models"),
  bench("10,000 files (synthetic)", map.syntheticTree(10_000).files.slice(0, SERVER_CAP), undefined, "src"),
);
const ok = worst <= BUDGET_MS;
console.log(ok ? `within the ${BUDGET_MS} ms budget` : `OVER the ${BUDGET_MS} ms budget`);
process.exitCode = ok ? 0 : 1;
