import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

/**
 * Every date becomes text through `src/lib/format.ts`, which renders in the
 * viewer's chosen zone and pins en-US so server and browser agree. A raw
 * `toLocaleString()` or `Intl.DateTimeFormat` call skips both: it renders in
 * whatever zone and locale the runtime has, which differs between the server
 * and the browser and breaks hydration. Numbers go through `formatNumber`
 * for the same reason, so the guard needs no date-or-number guessing.
 *
 * The web app has the same guard in `packages/web/src/lib`.
 */
const SRC = join(__dirname, "../src");

/** The one module allowed to call the raw APIs. */
const ALLOWED = new Set(["lib/format.ts"]);

/** `.toLocaleString(`, `.toLocaleDateString(`, `.toLocaleTimeString(`, any
 *  `Intl.DateTimeFormat(` call, and the local-zone `Date` readers
 *  (`.getHours()`, `.getDate()`, `.toDateString()`...), which read the
 *  runtime's zone instead of the viewer's. `getUTC*` stays allowed for code
 *  that works in UTC on purpose. Prose in comments has no leading dot or paren. */
const RAW =
  /\.toLocale(?:Date|Time)?String\(|\bIntl\.DateTimeFormat\(|\.get(?:Hours|Minutes|Date|Day|Month|FullYear)\(\)|\.to(?:Date|Time)String\(/;

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return walk(full);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [full] : [];
  });
}

describe("no raw date or number formatting outside format.ts", () => {
  it("routes every toLocale*String and Intl.DateTimeFormat call through format.ts", () => {
    const offenders: string[] = [];
    for (const file of walk(SRC)) {
      const rel = relative(SRC, file).split(sep).join("/");
      if (ALLOWED.has(rel)) continue;
      readFileSync(file, "utf8")
        .split("\n")
        .forEach((line, i) => {
          if (RAW.test(line)) offenders.push(`${rel}:${i + 1}: ${line.trim()}`);
        });
    }
    expect(offenders).toEqual([]);
  });

  it("catches the patterns it exists for", () => {
    expect(RAW.test('d.toLocaleDateString("en-US")')).toBe(true);
    expect(RAW.test("n.toLocaleString()")).toBe(true);
    expect(RAW.test("new Intl.DateTimeFormat(undefined, opts)")).toBe(true);
    expect(RAW.test("d.getHours()")).toBe(true);
    expect(RAW.test("d.toDateString()")).toBe(true);
    expect(RAW.test("d.getUTCDate()")).toBe(false);
    expect(RAW.test("// formatDate, not toLocaleDateString(): ...")).toBe(false);
  });
});
