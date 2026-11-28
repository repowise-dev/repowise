// @vitest-environment jsdom

import React from "react";
import { SWRConfig } from "swr";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SetupResponse } from "@/lib/api/setup";
import { doneFixture, setupFixture, stepFixture, stepsFixture } from "./setup-fixture";

const mocks = vi.hoisted(() => ({ getRepoSetup: vi.fn() }));

vi.mock("@/lib/api/setup", () => ({ getRepoSetup: mocks.getRepoSetup }));

import { SetupProgressMark, setupEntryRepoId, useSetupEntry } from "./setup-entry";

const repo = (id: string, status: string | null = null) =>
  ({ id, name: id, workspace_status: status }) as never;

function Entry({ activeRepoId }: { activeRepoId?: string }) {
  const { href, progress } = useSetupEntry([repo("r1")], activeRepoId);
  return (
    <a href={href}>
      Setup
      <SetupProgressMark progress={progress} />
    </a>
  );
}

function renderEntry(setup: SetupResponse | null, active = true) {
  if (setup) mocks.getRepoSetup.mockResolvedValue(setup);
  return render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <Entry activeRepoId={active ? "r1" : undefined} />
    </SWRConfig>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Setup entry", () => {
  it("counts the essentials while one is open and links to the repo's Setup", async () => {
    renderEntry(setupFixture());
    const link = await screen.findByRole("link", { name: "Setup , 2 of 3 essentials done" });
    expect(link.getAttribute("href")).toBe("/repos/r1/settings/setup");
    expect(link.textContent).toContain("2/3");
  });

  it("stays once the essentials are done, with a quiet done state", async () => {
    renderEntry(doneFixture());
    expect(await screen.findByRole("link", { name: "Setup , Set up" })).toBeTruthy();
  });

  it("shows the finish-setup steps repowise next offers, labelled apart from the essentials", async () => {
    const steps = ["coverage_stale", "evidence_found", "settings_suggested"].map((r) => stepFixture(r));
    renderEntry(doneFixture({ setup: stepsFixture(steps, { offer: "finish_setup" }) }));
    const link = await screen.findByRole("link", { name: /Essentials done\. 3 more steps to finish/ });
    expect(link.textContent).toContain("3 more");
  });

  it("reads done once the pass is dismissed or no longer offered", async () => {
    renderEntry(doneFixture({ setup: stepsFixture([stepFixture("coverage_stale")], { offer: null, dismissed: true }) }));
    expect(await screen.findByRole("link", { name: "Setup , Set up" })).toBeTruthy();
  });

  it("links to the workspace Setup and reads nothing with no indexed repo active", async () => {
    renderEntry(null, false);
    const link = screen.getByRole("link", { name: "Setup" });
    expect(link.getAttribute("href")).toBe("/settings/setup");
    await waitFor(() => expect(mocks.getRepoSetup).not.toHaveBeenCalled());
  });

  it("follows the active repo only once it has an index", () => {
    expect(setupEntryRepoId([repo("r1")], "r1")).toBe("r1");
    expect(setupEntryRepoId([repo("r1", "needs_index")], "r1")).toBeUndefined();
    expect(setupEntryRepoId([repo("ws:x")], "ws:x")).toBeUndefined();
    expect(setupEntryRepoId([repo("r1")], undefined)).toBeUndefined();
  });
});
