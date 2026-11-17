// @vitest-environment jsdom

import React from "react";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const nav = vi.hoisted(() => ({ pathname: "/settings/agents", replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ replace: nav.replace }),
}));

import {
  GLOBAL_SETTINGS_GROUPS,
  REPO_SETTINGS_GROUPS,
  SETTINGS_GROUPS,
  groupFromHash,
  repoSettingsHref,
  settingsHref,
  type SettingsGroupId,
} from "./settings-groups";
import { SettingsNav, SettingsRedirect } from "./settings-nav";

const titles = Object.fromEntries(SETTINGS_GROUPS.map((g) => [g, `T-${g}`])) as Record<
  SettingsGroupId,
  string
>;

afterEach(() => {
  cleanup();
  nav.replace.mockClear();
  window.location.hash = "";
});

describe("settings routes", () => {
  it("keeps Privacy last, and gives every group a page on disk", () => {
    expect(GLOBAL_SETTINGS_GROUPS.at(-1)).toBe("privacy");
    const app = join(__dirname, "..", "..", "app");
    for (const g of GLOBAL_SETTINGS_GROUPS) {
      expect(existsSync(join(app, "settings", g, "page.tsx")), g).toBe(true);
    }
    for (const g of REPO_SETTINGS_GROUPS) {
      expect(existsSync(join(app, "repos", "[id]", "settings", g, "page.tsx")), g).toBe(true);
    }
  });

  it("builds sub-page hrefs", () => {
    expect(settingsHref("models")).toBe("/settings/models");
    expect(repoSettingsHref("r1", "sync")).toBe("/repos/r1/settings/sync");
  });

  it("maps old in-page anchors to the sub-page they meant", () => {
    expect(groupFromHash("#provider", REPO_SETTINGS_GROUPS)).toBe("models");
    expect(groupFromHash("#group-sync", REPO_SETTINGS_GROUPS)).toBe("sync");
    expect(groupFromHash("#group-privacy", REPO_SETTINGS_GROUPS)).toBeNull();
    expect(groupFromHash("", GLOBAL_SETTINGS_GROUPS)).toBeNull();
  });
});

describe("SettingsNav", () => {
  it("links every group and marks the current one", () => {
    nav.pathname = "/settings/agents";
    render(<SettingsNav base="/settings" groups={GLOBAL_SETTINGS_GROUPS} titles={titles} label="Sections" />);
    const links = screen.getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(
      GLOBAL_SETTINGS_GROUPS.map((g) => `/settings/${g}`),
    );
    expect(screen.getByRole("link", { name: "T-agents" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "T-models" }).getAttribute("aria-current")).toBeNull();
  });
});

describe("SettingsRedirect", () => {
  it("opens the first group by default", () => {
    render(<SettingsRedirect base="/repos/r1/settings" groups={REPO_SETTINGS_GROUPS} />);
    expect(nav.replace).toHaveBeenCalledWith("/repos/r1/settings/setup");
  });

  it("follows an old #provider link to Models", () => {
    window.location.hash = "#provider";
    render(<SettingsRedirect base="/repos/r1/settings" groups={REPO_SETTINGS_GROUPS} />);
    expect(nav.replace).toHaveBeenCalledWith("/repos/r1/settings/models");
  });
});
