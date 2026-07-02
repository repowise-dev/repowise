// Bundles the extension host entry into a single CommonJS file that VS Code
// loads. 'vscode' is provided by the host at runtime and must stay external.
// The workspace TypeScript packages (@repowise-dev/*) ship as raw source and
// are bundled in here, so the published extension carries zero runtime deps.

import { build, context } from "esbuild";
import { statSync } from "node:fs";

const watch = process.argv.includes("--watch");
const production = process.argv.includes("--production") || !watch;

// The host bundle must stay small so activation stays fast.
const SIZE_BUDGET_BYTES = 300 * 1024;
const OUTFILE = "dist/extension.js";

/** @type {import("esbuild").BuildOptions} */
const options = {
  entryPoints: ["src/extension.ts"],
  outfile: OUTFILE,
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node20",
  external: ["vscode"],
  minify: production,
  sourcemap: !production,
  logLevel: "info",
};

function assertUnderBudget() {
  const bytes = statSync(OUTFILE).size;
  const kb = (bytes / 1024).toFixed(1);
  if (bytes > SIZE_BUDGET_BYTES) {
    console.error(
      `Bundle ${OUTFILE} is ${kb} KB, over the ${SIZE_BUDGET_BYTES / 1024} KB budget.`,
    );
    process.exit(1);
  }
  console.log(`Bundle ${OUTFILE} is ${kb} KB (budget ${SIZE_BUDGET_BYTES / 1024} KB).`);
}

if (watch) {
  const ctx = await context(options);
  await ctx.watch();
  console.log("esbuild watching for changes...");
} else {
  await build(options);
  assertUnderBudget();
}
