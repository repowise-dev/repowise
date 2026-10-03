// Local benchmark for the map's per-frame work at 180x50 on the recorded
// Django feed: layout, a ripple frame, and the cell encoding. Budget 16 ms per
// frame; vitest guards at 5x that (test/overlay.test.ts).
//
//   npm run bench:map -w @repowise-dev/claude-mod

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const BUDGET_MS = 16;
const RUNS = 50;
const pkg = resolve(dirname(fileURLToPath(import.meta.url)), "..");

// test/setup gives Node the runtime's Uint8Array.prototype.toBase64.
const bundled = await build({
  stdin: {
    contents: 'import "./test/setup"; export * from "./src/views/map"; export * from "./src/views/overlay"; export * from "./src/model/trail";',
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
const feed = fixture("django-health-map.json");
const importers = fixture("django-blast-radius-query-depth1.json").transitive_affected.map((t) => t.path);
const root = "C:/work/django";
let trail = map.initialTrail;
for (const p of ["django/db/models/base.py", "django/db/models/manager.py"]) {
  trail = map.reduceTrail(trail, { type: "read", path: `c:/work/django/${p}` });
}
trail = map.reduceTrail(trail, { type: "edit", path: "c:/work/django/django/db/models/query.py" });
trail = map.reduceTrail(trail, { type: "callers", edits: 1, callers: { status: "ready", paths: importers } });

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
  console.log(`${label.padEnd(32)} median ${median.toFixed(2)} ms  p95 ${p95.toFixed(2)} ms`);
  return p95;
}

const layout = map.layoutMap(feed.files, 180, 50, true);
const overlay = map.resolveOverlay(layout, trail, root).overlay;
console.log(`Django feed: ${feed.files.length} files, ${layout.drawn.length} drawn at 180x50`);
const layoutP95 = time("layout (on resize only)", () => map.layoutMap(feed.files, 180, 50, true));
const frameP95 = time("ripple frame + encode", (i) =>
  map.frameCells(layout, map.resolveOverlay(layout, trail, root).overlay, { kind: "ripple", t: (i % 30) / 30 }),
);
time("resting frame + encode", () => map.frameCells(layout, overlay));
const ok = frameP95 <= BUDGET_MS && layoutP95 <= BUDGET_MS;
console.log(ok ? `within the ${BUDGET_MS} ms budget` : `OVER the ${BUDGET_MS} ms budget`);
process.exitCode = ok ? 0 : 1;
