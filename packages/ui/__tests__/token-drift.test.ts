import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

/**
 * Drift guard for token *names*.
 *
 * `brand.test.ts` pins token values. This pins their existence, which is a
 * different failure: Tailwind's arbitrary-value syntax will happily emit
 * `background: var(--color-bg-secondary)` for a name no stylesheet has ever
 * carried. Nothing errors, nothing logs, and the element renders without the
 * ground or border it claims.
 *
 * A sweep at the time this was written found seven such names across ~45 call
 * sites — including `--color-bg-muted`, the track of every proportion bar on
 * Code Health and Dead code, which had been rendering transparent. They are
 * invisible precisely because a missing ground reads as a design choice.
 *
 * `var(--name, fallback)` is legal and deliberate; those are skipped.
 */

const UI_SRC = join(__dirname, "../src");
const CSS = join(__dirname, "../styles/globals.css");

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(tsx?|css)$/.test(entry)) out.push(p);
  }
  return out;
}

function definedTokens(): Set<string> {
  const css = readFileSync(CSS, "utf8");
  const names = css.match(/--color-[a-z0-9-]+\s*:/g) ?? [];
  return new Set(names.map((n) => n.replace(/\s*:$/, "")));
}

/** Every `var(--color-x)` with no fallback, mapped to the files using it. */
function referencedTokens(files: string[]): Map<string, string[]> {
  const refs = new Map<string, string[]>();
  for (const file of files) {
    const text = readFileSync(file, "utf8");
    // The negative lookahead skips `var(--name, fallback)`.
    for (const m of text.matchAll(/var\((--color-[a-z0-9-]+)\s*\)/g)) {
      const name = m[1]!;
      const seen = refs.get(name) ?? [];
      if (!seen.includes(file)) seen.push(file);
      refs.set(name, seen);
    }
  }
  return refs;
}

describe("token drift", () => {
  it("references no colour token that globals.css does not define", () => {
    const defined = definedTokens();
    const referenced = referencedTokens(walk(UI_SRC));

    const missing = [...referenced.entries()]
      .filter(([name]) => !defined.has(name))
      .map(([name, files]) => {
        const rel = files.map((f) => f.replace(UI_SRC, "src")).slice(0, 3);
        return `${name} (${files.length} file(s): ${rel.join(", ")})`;
      })
      .sort();

    expect(missing).toEqual([]);
  });

  it("finds tokens at all, so a broken matcher cannot pass vacuously", () => {
    const referenced = referencedTokens(walk(UI_SRC));
    expect(referenced.size).toBeGreaterThan(50);
    expect(definedTokens().size).toBeGreaterThan(50);
  });

  it("ignores the deliberate var(--name, fallback) form", () => {
    const defined = definedTokens();
    // Both halves of the pair the churn ledger used to inline are real tokens
    // now, so the ledger tracks the theme instead of a hardcoded paper green.
    expect(defined.has("--color-success-muted")).toBe(true);
    expect(defined.has("--color-error-muted")).toBe(true);
  });
});
