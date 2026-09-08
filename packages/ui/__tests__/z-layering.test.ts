import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Ordering guard for the `--z-*` scale.
 *
 * `token-drift.test.ts` pins that a token *exists*. This pins the one thing
 * about the z scale that carries meaning: the order.
 *
 * The failure it exists to catch is silent. Radix portals Select, Popover and
 * Tooltip content to `document.body`, so a Select opened inside a Dialog is a
 * *sibling* of the dialog portal, not a child of it — the DOM nesting that
 * would normally settle paint order is gone, and the raw z-index values decide.
 * While `--z-dropdown` sat at 20 and `--z-modal` at 40, the wiki style Select in
 * the Add Repository dialog opened behind the dialog overlay. Nothing threw,
 * nothing logged, and the listbox still took focus, so arrow keys changed the
 * value under a dropdown the user could not see.
 *
 * No render test can catch this: jsdom has no paint and no stacking contexts,
 * and the wizard's own test renders the Select happily today. The assertion has
 * to be on the scale itself.
 */

const CSS = join(__dirname, "../styles/globals.css");
const UI_SRC = join(__dirname, "../src");

/** Surfaces own a region of the page. */
const SURFACES = ["--z-base", "--z-elevated", "--z-sidebar", "--z-modal", "--z-command"];
/** Floating layers are transient and anchored to a control on a surface. */
const FLOATING = ["--z-dropdown"];

function zTokens(): Map<string, number> {
  const css = readFileSync(CSS, "utf8");
  const out = new Map<string, number>();
  for (const m of css.matchAll(/(--z-[a-z0-9-]+)\s*:\s*(\d+)\s*;/g)) {
    out.set(m[1]!, Number(m[2]!));
  }
  return out;
}

function z(name: string): number {
  const value = zTokens().get(name);
  if (value === undefined) throw new Error(`${name} is not defined in globals.css`);
  return value;
}

describe("z-index layering", () => {
  it("puts every floating layer above every surface", () => {
    const inverted: string[] = [];
    for (const float of FLOATING) {
      for (const surface of SURFACES) {
        if (z(float) <= z(surface)) {
          inverted.push(`${float} (${z(float)}) must be above ${surface} (${z(surface)})`);
        }
      }
    }
    expect(inverted).toEqual([]);
  });

  it("clears the command palette, which hardcodes calc(var(--z-modal) + 1)", () => {
    // packages/web/src/components/search/command-palette.tsx does not read
    // --z-command; it offsets --z-modal directly. A dropdown that only cleared
    // --z-modal would still open behind the palette.
    expect(z("--z-dropdown")).toBeGreaterThan(z("--z-modal") + 1);
  });

  it("keeps toasts above everything, floating layers included", () => {
    for (const name of [...SURFACES, ...FLOATING]) {
      expect(z("--z-toast")).toBeGreaterThan(z(name));
    }
  });

  it("finds the whole scale, so a broken matcher cannot pass vacuously", () => {
    const tokens = zTokens();
    expect(tokens.size).toBe(7);
    for (const name of [...SURFACES, ...FLOATING, "--z-toast"]) {
      expect(tokens.has(name)).toBe(true);
    }
  });

  /**
   * The ordering above only means something while the components still read the
   * tokens this test sorts. If a portalled layer is switched to a bare `z-50`,
   * the scale can stay perfectly ordered and the bug comes straight back.
   */
  it("keeps the portalled layers on --z-dropdown and the dialog on --z-modal", () => {
    const onDropdown = ["ui/select.tsx", "ui/popover.tsx", "ui/tooltip.tsx"];
    for (const file of onDropdown) {
      const text = readFileSync(join(UI_SRC, file), "utf8");
      expect(text, `${file} should position itself with --z-dropdown`).toContain(
        "z-[var(--z-dropdown)]",
      );
    }

    const dialog = readFileSync(join(UI_SRC, "ui/dialog.tsx"), "utf8");
    // Overlay and content both, or the listbox clears one and not the other.
    expect(dialog.match(/z-\[var\(--z-modal\)\]/g) ?? []).toHaveLength(2);
  });
});
