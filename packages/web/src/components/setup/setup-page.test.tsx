// @vitest-environment jsdom

import React from "react";
import { SWRConfig } from "swr";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SetupResponse } from "@/lib/api/setup";
import { doneFixture, NOW, setupFixture, stepFixture, stepsFixture } from "./setup-fixture";

const mocks = vi.hoisted(() => ({
  getRepoSetup: vi.fn(),
  setSetupHook: vi.fn(),
  updateOnboardingState: vi.fn(),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
  setActionState: vi.fn(),
  evidenceStatus: "no_runtime_data",
}));

vi.mock("@/lib/api/setup", () => ({
  getRepoSetup: mocks.getRepoSetup,
  setSetupHook: mocks.setSetupHook,
  updateOnboardingState: mocks.updateOnboardingState,
  connectSetupAgent: vi.fn(),
  disconnectSetupAgent: vi.fn(),
  addSetupCoverage: vi.fn(),
  getOnboardingState: vi.fn(),
}));
vi.mock("@/lib/api/actions", () => ({ setActionState: mocks.setActionState }));
vi.mock("@/lib/api/runtime-evidence", () => ({
  getEvidenceStatus: async () => ({ status: mocks.evidenceStatus }),
}));
vi.mock("sonner", () => ({ toast: { error: mocks.toastError, success: mocks.toastSuccess } }));
// The sheet pulls in the providers and bulk generate flows; Setup only opens it.
vi.mock("@/components/model-upgrade/model-upgrade", () => ({
  useModelUpgrade: () => ({ openSheet: vi.fn(), sheet: null }),
}));

import { SetupPage } from "./setup-page";
import { hostedCardVisible } from "./hosted-setup-card";

function renderPage(setup: SetupResponse) {
  mocks.getRepoSetup.mockResolvedValue(setup);
  return render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <SetupPage repoId="r1" repoName="orbit-api" />
    </SWRConfig>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  // Rows are derived against the wall clock; pin it to the fixture's moment.
  // Only Date is faked, so SWR and findBy* timers still run.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
  window.location.hash = "";
  mocks.evidenceStatus = "no_runtime_data";
});

describe("SetupPage", () => {
  it("shows the essentials count and the tour link", async () => {
    renderPage(setupFixture());
    expect(await screen.findByText(/2 of 3 essentials done/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Take the tour" }).getAttribute("href")).toBe(
      "/repos/r1/overview?tour=1",
    );
  });

  it("links each row to the settings page that holds its control, once", async () => {
    renderPage(setupFixture());
    expect(await screen.findByText(/2 of 3 essentials done/)).toBeTruthy();
    // The switches and buttons live on the settings pages, not here too.
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByRole("button", { name: "Connect Cursor" })).toBeNull();
    const hrefs = screen
      .getAllByRole("link", { name: /^Change in / })
      .map((a) => a.getAttribute("href"));
    expect(hrefs).toContain("/repos/r1/settings/sync");
    expect(hrefs).toContain("/repos/r1/settings/agents");
  });

  it("gives a Needs attention row the copy action, a status for screen readers and no preview box", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    const s = setupFixture();
    renderPage({ ...s, index: { ...s.index, parse_error_count: 1, parse_error_files: ["gen.py"] } });
    expect(await screen.findByText("Needs attention")).toBeTruthy();
    expect(screen.queryByText("Prompt preview")).toBeNull();
    const row = document.querySelector("[data-row='index-parse']") as HTMLElement;
    expect(row.className).not.toMatch(/warning-muted/);
    expect(row.querySelector("h3 .sr-only")?.textContent).toContain("1");

    const copy = screen.getByRole("button", { name: "Copy prompt: Files that could not be parsed" });
    fireEvent.click(copy);
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const prompt = writeText.mock.calls[0]![0] as string;
    expect(prompt).toContain("repowise reports for orbit-api: Files that could not be parsed.");
    expect(prompt).toContain("- `gen.py`");
    expect(prompt).not.toMatch(/to confirm|until I confirm/);
    expect(await screen.findByText("Copied", { selector: "[aria-live='polite']" })).toBeTruthy();
  });

  it("clamps long doctor detail to one line behind a Show details toggle", async () => {
    // jsdom does not lay out text, so report the clamped line as overflowing.
    const height = vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(40);
    renderPage(
      setupFixture({
        checks: [
          { id: "vector-store", name: "Vector store", level: "fail", detail: "missing (run repowise reindex)", fix: "repowise reindex" },
        ],
      }),
    );
    await screen.findByText("Vector store");
    const row = within(document.querySelector("[data-row='check-vector-store']") as HTMLElement);
    const toggle = row.getByRole("button", { name: "Show details" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    // The "(run X)" hint repeats the command chip, so it is gone from the detail.
    expect(row.getByText("missing")).toBeTruthy();
    fireEvent.click(toggle);
    expect(row.getByRole("button", { name: "Hide details" }).getAttribute("aria-expanded")).toBe("true");
    height.mockRestore();
  });
});

describe("agent hook scripts row", () => {
  it("lists a hook that cannot find its script under Needs attention with its fix", async () => {
    renderPage(
      setupFixture({
        checks: [
          {
            id: "agent-hook-scripts",
            name: "Agent hook scripts",
            level: "warn",
            detail: "not found, so those hooks do nothing: repowise-augment (repo/.codex/hooks.json). Add C:/env/Scripts to PATH.",
            fix: null,
          },
        ],
      }),
    );
    await screen.findByText("Agent hook scripts");
    const row = within(document.querySelector("[data-row='check-agent-hook-scripts']") as HTMLElement);
    expect(row.getByText(/Add C:\/env\/Scripts to PATH\./)).toBeTruthy();
    // No single command fixes a PATH problem, so the row falls back to doctor.
    expect(row.getByText("repowise doctor --format json")).toBeTruthy();
  });
});

describe("unreadable sections", () => {
  it("says doctor couldn't run instead of showing an empty Needs attention", async () => {
    renderPage(setupFixture({ checks_available: false, checks: [] }));
    expect(await screen.findByText("Needs attention")).toBeTruthy();
    expect(
      screen.getByText("Couldn't read this here. Run repowise doctor in a terminal to check."),
    ).toBeTruthy();
  });

  it("shows the unreadable line for coverage in place of its empty state", async () => {
    renderPage(setupFixture({ coverage_available: false }));
    expect(
      await screen.findByText("Couldn't read this here. Run repowise coverage status in a terminal to check."),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Use this report" })).toBeNull();
  });
});

describe("hosted card", () => {
  it("appears once the essentials are done, and × hides it on this machine", async () => {
    mocks.updateOnboardingState.mockResolvedValue({ ...doneFixture().onboarding, hosted_card_hidden: true });
    renderPage(doneFixture());
    expect(await screen.findByText("You're set up")).toBeTruthy();
    expect(screen.getByText("All of this, run for you and your team")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Start free" }).getAttribute("href")).toBe(
      "https://repowise.dev/?src=local_web_setup_card",
    );
    expect(screen.getByRole("link", { name: "See what hosted adds" }).getAttribute("href")).toBe(
      "https://repowise.dev/hosted?src=local_web_setup_card",
    );

    fireEvent.click(screen.getByRole("button", { name: "Hide on this machine" }));
    expect(screen.queryByText("All of this, run for you and your team")).toBeNull();
    expect(mocks.updateOnboardingState).toHaveBeenCalledWith({ hosted_card_hidden: true });
  });

  it("is not shown before the essentials are done", async () => {
    renderPage(setupFixture());
    await screen.findByText(/2 of 3 essentials done/);
    expect(screen.queryByText("All of this, run for you and your team")).toBeNull();
  });

  it("stays hidden with tips off, when signed in, or when hidden before", () => {
    const on = doneFixture().onboarding;
    expect(hostedCardVisible(on, false)).toBe(true);
    expect(hostedCardVisible({ ...on, hints_enabled: false }, false)).toBe(false);
    expect(hostedCardVisible({ ...on, signed_in: true }, false)).toBe(false);
    expect(hostedCardVisible({ ...on, hosted_card_hidden: true }, false)).toBe(false);
    expect(hostedCardVisible(on, true)).toBe(false);
  });
});

describe("finish setup hero", () => {
  const offered = () =>
    doneFixture({
      sync: { installed: false },
      setup: stepsFixture([stepFixture("sync_hook_missing")], {
        offer: "finish_setup",
        first_call_at: "2026-10-25T11:56:00Z",
        agent_calls: 3,
      }),
    });

  it("offers the pass once an agent has called, with one copy button and the CLI twin", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderPage(offered());
    const heading = await screen.findByRole("heading", { name: "Finish setup with your agent" });
    expect(within(heading.closest("section") as HTMLElement).getByText(/Claude Code made its first call/)).toBeTruthy();
    expect(screen.getByText("repowise next --prompt setup")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Copy prompt for your agent" }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText.mock.calls[0]![0]).toContain("Finish setting up Repowise for this repository.");
  });

  it("is absent without the server's offer, even with steps left", async () => {
    renderPage(setupFixture({ setup: stepsFixture([stepFixture("sync_hook_missing")], { offer: null }) }));
    await screen.findByText(/2 of 3 essentials done/);
    expect(screen.queryByText("Finish setup with your agent")).toBeNull();
  });

  it("Not now dismisses it for this repo", async () => {
    mocks.setActionState.mockResolvedValue({ action_id: "setup", state: "dismissed", until: null });
    renderPage(offered());
    fireEvent.click(await screen.findByRole("button", { name: "Not now" }));
    expect(screen.queryByText("Finish setup with your agent")).toBeNull();
    expect(mocks.setActionState).toHaveBeenCalledWith("r1", "setup", { state: "dismissed" });
  });
});

describe("rows a setup rule raises", () => {
  it("marks the row a #rule link names, and keeps #row-id links working", async () => {
    window.location.hash = "#sync_hook_missing";
    renderPage(setupFixture({ sync: { installed: false } }));
    await screen.findByText(/essentials done/);
    const sync = document.querySelector("[data-row='sync']") as HTMLElement;
    await waitFor(() => expect(sync.dataset.linked).toBe("true"));
    expect(document.getElementById("rewrite")?.getAttribute("data-row")).toBe("rewrite");
    expect(document.getElementById("docs_model_ready")?.closest("[data-row]")?.getAttribute("data-row")).toBe("model");
  });

  it("names the unsynced commits on the sync row", async () => {
    renderPage(
      setupFixture({
        sync: { installed: false },
        setup: stepsFixture([
          stepFixture("sync_hook_missing", { why: [{ label: "commits since last sync", value: "1,038", basis: "measured" }] }),
        ]),
      }),
    );
    expect(await screen.findByText("1,038 commits since the last sync.")).toBeTruthy();
  });

  it("shows performance evidence found on disk, with the connect-evidence prompt", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderPage(
      setupFixture({
        setup: stepsFixture([
          stepFixture("evidence_found", {
            surface: "performance",
            details: [{ path: ".benchmarks/run.json", line: null, symbol: null, marker: null, severity: null, reason: "pytest-benchmark", ref: null }],
          }),
        ]),
      }),
    );
    const row = within((await screen.findByText("Performance evidence")).closest("[data-row]") as HTMLElement);
    expect(row.getByText("repowise evidence setup")).toBeTruthy();
    fireEvent.click(row.getByRole("button", { name: /Copy prompt/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText.mock.calls[0]![0]).toContain("repowise evidence setup --format json");
  });

  it("says evidence is on once runs are stored", async () => {
    mocks.evidenceStatus = "measured";
    renderPage(setupFixture({ setup: stepsFixture([]) }));
    const row = (await screen.findByText("Performance evidence")).closest("[data-row]") as HTMLElement;
    await waitFor(() => expect(row.textContent).toContain("Runtime evidence is stored"));
  });

  it("lists rows usage unlocked under Suggested from your usage", async () => {
    renderPage(
      setupFixture({
        setup: stepsFixture([
          stepFixture("always_load_suggested", {
            title: "Load the four read tools up front",
            impact: "Costs about 2.5k tokens a turn.",
            command: "repowise config set mcp.always_load true",
          }),
        ]),
      }),
    );
    expect(await screen.findByText("Suggested from your usage")).toBeTruthy();
    const row = document.getElementById("always_load_suggested")?.closest("[data-row]") as HTMLElement;
    expect(row.textContent).toContain("Load the four read tools up front");
    expect(row.textContent).toContain("repowise config set mcp.always_load true");
  });
});
