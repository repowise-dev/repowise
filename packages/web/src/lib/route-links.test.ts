import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

/**
 * Drift guard for links into routes that are only redirects.
 *
 * Sections get merged and their old routes stay behind as one-line redirect
 * stubs, which is right — people have those URLs bookmarked. What is not right
 * is our own navigation still pointing at them. Nothing errors: the click
 * works, it just costs an extra hop, and the destination silently stops being
 * the one the label promised. `/risk` with no tab lands on Code Health's
 * Overview, so a link labelled "Review threshold" arrived somewhere that never
 * mentions one.
 *
 * A sweep at the time this was written found eleven such links across six
 * routes, every one of them from a stat or section heading that named
 * something the destination did not show.
 */

const WEB_SRC = join(__dirname, "..");
const UI_SRC = join(__dirname, "../../../ui/src");
const ROUTES = join(WEB_SRC, "app/repos/[id]");

/**
 * Deep links we keep pointing at a redirect on purpose. `/wiki/{pageId}` is a
 * documented permanent entry point, and the repo root is where a repo card is
 * supposed to land.
 */
const DELIBERATE = new Set(["wiki"]);

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.tsx?$/.test(entry)) out.push(p);
  }
  return out;
}

/**
 * Top-level route segments under `/repos/{id}` whose page only redirects.
 *
 * A stub has no JSX to return — that is what separates it from a real page
 * that happens to redirect on one branch, such as an auth guard.
 */
function redirectOnlySegments(): Set<string> {
  const out = new Set<string>();
  for (const entry of readdirSync(ROUTES)) {
    if (entry.startsWith("[")) continue;
    const dir = join(ROUTES, entry);
    if (!statSync(dir).isDirectory()) continue;
    let page: string;
    try {
      page = readFileSync(join(dir, "page.tsx"), "utf8");
    } catch {
      continue;
    }
    if (page.includes("redirect") && !page.includes("return (")) out.add(entry);
  }
  return out;
}

/** Every `${base}/seg` or `/repos/${id}/seg` written on a line carrying an href. */
function linkedSegments(files: string[]): Map<string, string[]> {
  const pattern = /(?:\$\{[A-Za-z_][A-Za-z0-9_]*\}|\/repos\/\$\{[^}]+\})\/([a-z][a-z-]*)/g;
  const found = new Map<string, string[]>();
  for (const file of files) {
    const lines = readFileSync(file, "utf8").split("\n");
    lines.forEach((line, i) => {
      if (!line.includes("href") && !line.includes("Href")) return;
      for (const m of line.matchAll(pattern)) {
        const seg = m[1]!;
        const at = `${file}:${i + 1}`;
        const seen = found.get(seg) ?? [];
        if (!seen.includes(at)) seen.push(at);
        found.set(seg, seen);
      }
    });
  }
  return found;
}

describe("route links", () => {
  it("points at no route that is only a redirect", () => {
    const stubs = redirectOnlySegments();
    const linked = linkedSegments([...walk(WEB_SRC), ...walk(UI_SRC)]);

    const offenders = [...linked.entries()]
      .filter(([seg]) => stubs.has(seg) && !DELIBERATE.has(seg))
      .map(([seg, at]) => `/${seg} <- ${at.join(", ")}`)
      .sort();

    expect(offenders).toEqual([]);
  });

  it("finds stubs and links at all, so a broken matcher cannot pass vacuously", () => {
    expect(redirectOnlySegments().size).toBeGreaterThan(5);
    expect(linkedSegments([...walk(WEB_SRC), ...walk(UI_SRC)]).size).toBeGreaterThan(10);
  });

  it("points at no Code Health tab that only survives as a legacy alias", () => {
    const page = readFileSync(join(ROUTES, "code-health/page.tsx"), "utf8");

    const tabs = new Set(
      [...(page.match(/const TABS = \[([\s\S]*?)\] as const/)?.[1] ?? "").matchAll(/"([^"]+)"/g)].map(
        (m) => m[1]!,
      ),
    );
    const aliases = new Set(
      [
        ...(page.match(/const TAB_ALIASES[^{]*\{([\s\S]*?)\n\}/)?.[1] ?? "").matchAll(
          /^\s*"?([a-z-]+)"?:/gm,
        ),
      ].map((m) => m[1]!),
    );
    // The parse has to have found something, or this passes on a rename.
    expect(tabs.size).toBeGreaterThan(3);
    expect(aliases.size).toBeGreaterThan(1);

    const offenders: string[] = [];
    for (const file of [...walk(WEB_SRC), ...walk(UI_SRC)]) {
      readFileSync(file, "utf8")
        .split("\n")
        .forEach((line, i) => {
          for (const m of line.matchAll(/code-health\?tab=([a-z-]+)/g)) {
            const tab = m[1]!;
            // An alias redirects, so the link works — it just lands somewhere
            // other than the label promises. `?tab=modules` reads "Modules"
            // and arrives at Overview.
            if (!tabs.has(tab) && aliases.has(tab)) offenders.push(`?tab=${tab} <- ${file}:${i + 1}`);
          }
        });
    }

    expect(offenders.sort()).toEqual([]);
  });

  it("still recognises the deep links we keep on purpose", () => {
    // If `/wiki` stops being a redirect this allowance is dead weight, and the
    // set should shrink rather than quietly cover a real page.
    expect(redirectOnlySegments().has("wiki")).toBe(true);
  });
});
