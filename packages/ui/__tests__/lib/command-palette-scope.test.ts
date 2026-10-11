import { afterEach, describe, expect, it, vi } from "vitest";
import {
  claimCommandPaletteShortcut,
  commandPaletteShortcutIsClaimed,
} from "../../src/lib/command-palette-scope.js";

afterEach(() => {
  document.body.removeAttribute("data-command-palette-scope");
  document.body.removeAttribute("data-command-palette-holders");
  vi.restoreAllMocks();
});

describe("commandPaletteShortcutIsClaimed", () => {
  it("is unclaimed until something claims it", () => {
    expect(commandPaletteShortcutIsClaimed()).toBe(false);
  });

  it("is claimed while a scoped palette is mounted", () => {
    claimCommandPaletteShortcut("docs");
    expect(commandPaletteShortcutIsClaimed()).toBe(true);
  });

  it("is released when that palette unmounts", () => {
    const release = claimCommandPaletteShortcut("docs");
    release();
    expect(commandPaletteShortcutIsClaimed()).toBe(false);
  });

  it("stays claimed until the last of several holders releases", () => {
    const a = claimCommandPaletteShortcut("docs");
    const b = claimCommandPaletteShortcut("docs");
    a();
    expect(commandPaletteShortcutIsClaimed()).toBe(true);
    b();
    expect(commandPaletteShortcutIsClaimed()).toBe(false);
  });

  it("survives a remount that releases out of order", () => {
    const first = claimCommandPaletteShortcut("docs");
    const second = claimCommandPaletteShortcut("docs");
    second();
    first();
    expect(commandPaletteShortcutIsClaimed()).toBe(false);
  });
});

describe("claimCommandPaletteShortcut noise", () => {
  it("warns when two different scopes hold the shortcut at once", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    claimCommandPaletteShortcut("docs");
    claimCommandPaletteShortcut("graph");
    expect(warn).toHaveBeenCalledOnce();
    expect(String(warn.mock.calls[0]?.[0])).toContain("graph");
  });

  it("warns when a release runs twice rather than going negative", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const release = claimCommandPaletteShortcut("docs");
    release();
    release();
    expect(commandPaletteShortcutIsClaimed()).toBe(false);
    expect(warn).toHaveBeenCalledOnce();
  });
});
