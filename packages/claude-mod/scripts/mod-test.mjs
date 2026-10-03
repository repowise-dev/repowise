// Runs mod-test/*.test.ts under `claude plugin test` against the committed
// bundle. The tests go into a temporary copy of plugins/claude-code so they
// never ship with the plugin. Needs the `claude` CLI (>= 2.1.287) on PATH.

import { spawnSync } from "node:child_process";
import { cpSync, mkdtempSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const pkg = resolve(here, "..");
const plugin = resolve(pkg, "../../plugins/claude-code");

const work = mkdtempSync(join(tmpdir(), "lens-mod-test-"));
const copy = join(work, "claude-code");
try {
  cpSync(plugin, copy, {
    recursive: true,
    // Claude Code writes these into a plugin dir it loads; never copy stale ones.
    filter: (src) => !src.includes(join(".claude-plugin", "types")) && !src.endsWith("tsconfig.json"),
  });
  for (const name of readdirSync(join(pkg, "mod-test"))) {
    if (name.endsWith(".test.ts")) cpSync(join(pkg, "mod-test", name), join(copy, "tests", name));
  }
  const run = spawnSync("claude", ["plugin", "test", copy], { stdio: "inherit", shell: process.platform === "win32" });
  process.exitCode = run.status ?? 1;
} finally {
  rmSync(work, { recursive: true, force: true });
}
