// @vitest-environment jsdom

import React from "react";
import { SWRConfig } from "swr";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RepoBreadcrumb } from "@/components/layout/repo-breadcrumb";
import { NoProviderReason } from "@/components/chat/no-provider-reason";
import { useModelUpgrade } from "./model-upgrade";

const mocks = vi.hoisted(() => ({
  getProviders: vi.fn(),
  setActiveProvider: vi.fn(),
  addProviderKey: vi.fn(),
  generateEstimate: vi.fn(),
  generatePages: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/repos/r1/docs",
  useRouter: () => ({ refresh: mocks.refresh, push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));
vi.mock("@/lib/api/providers", () => ({
  getProviders: mocks.getProviders,
  setActiveProvider: mocks.setActiveProvider,
  addProviderKey: mocks.addProviderKey,
}));
vi.mock("@/lib/api/repos", () => ({
  generateEstimate: mocks.generateEstimate,
  generatePages: mocks.generatePages,
}));
vi.mock("@/components/jobs/generation-progress-wrapper", () => ({
  GenerationProgressWrapper: ({ jobId }: { jobId: string }) => <div>progress {jobId}</div>,
}));

const PROVIDERS = {
  active: { provider: null, model: null },
  providers: [
    { id: "openai", name: "OpenAI", models: [], default_model: "", configured: true, requires_key: true, detected: "env" },
    { id: "ollama", name: "Ollama (Local)", models: [], default_model: "", configured: true, requires_key: false, local_model: true, detected: null },
    { id: "claude_cli", name: "Claude Code (Local CLI)", models: [], default_model: "", configured: true, requires_key: false, detected: "signed_in" },
  ],
};

function estimate(provider: string | null, cost: number | null) {
  return {
    total_pages: 46,
    pages_by_type: {},
    pages_to_mark_stale: 0,
    unknown_page_ids: [],
    provider: { name: provider, model: provider ? "m" : null, error: null },
    estimate:
      cost == null
        ? null
        : {
            estimated_cost_usd: cost,
            cost_low_usd: null,
            cost_high_usd: null,
            estimated_input_tokens: 1,
            estimated_output_tokens: 1,
            is_calibrated: false,
          },
  };
}

/** The chat surfaces' wiring: the sheet lives beside the composer and the
 *  no-provider reason only opens it. */
function ChatHarness({ message }: { message: "noProvider" | "noProviderShort" }) {
  const upgrade = useModelUpgrade("r1");
  return (
    <>
      <NoProviderReason message={message} onAddModel={upgrade.openSheet} />
      {upgrade.sheet}
    </>
  );
}

function renderFresh(ui: React.ReactElement) {
  return render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{ui}</SWRConfig>);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getProviders.mockResolvedValue(PROVIDERS);
  mocks.setActiveProvider.mockResolvedValue(PROVIDERS);
  mocks.generateEstimate.mockImplementation(
    async (_repo: string, _body: unknown, options: { provider?: string } = {}) =>
      estimate(options.provider ?? null, options.provider ? 0.42 : null),
  );
  mocks.generatePages.mockResolvedValue({ job_id: "j1", stream_token: "t" });
});
afterEach(cleanup);

describe("ways in to the model sheet", () => {
  it("the docs-mode badge opens it, and only then detects sources", async () => {
    renderFresh(<RepoBreadcrumb repoName="orbit-api" docsMode="deterministic" />);
    expect(mocks.getProviders).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Write with a model/ }));

    expect(await screen.findByText("Write this documentation with a model")).toBeTruthy();
    expect(mocks.getProviders).toHaveBeenCalledWith("r1", { detect: true });
    expect(await screen.findByText("Uses your Claude Code sign-in.")).toBeTruthy();
  });

  it("the chat no-provider state opens it and keeps the settings link", async () => {
    renderFresh(<ChatHarness message="noProvider" />);
    expect(screen.getByRole("link", { name: "settings" }).getAttribute("href")).toBe("/settings/models");

    fireEvent.click(screen.getByRole("button", { name: "Add a model" }));

    expect(await screen.findByText("Write this documentation with a model")).toBeTruthy();
  });
});

describe("writing the docs", () => {
  async function openSheet() {
    renderFresh(<ChatHarness message="noProviderShort" />);
    fireEvent.click(screen.getByRole("button", { name: "Add a model" }));
    await screen.findByText("46 pages to write");
  }

  it("a sign-in sets the repo's provider and starts right away", async () => {
    await openSheet();
    fireEvent.click(screen.getByRole("button", { name: "Write the docs" }));

    await waitFor(() =>
      expect(mocks.generatePages).toHaveBeenCalledWith("r1", {
        selection: { kind: "unwritten" },
        cascade: "none",
      }),
    );
    expect(mocks.setActiveProvider).toHaveBeenCalledWith("claude_cli", undefined, "r1");
    expect(await screen.findByText("progress j1")).toBeTruthy();
  });

  it("a key shows its price in the sheet, then confirms before it runs", async () => {
    await openSheet();
    fireEvent.click(screen.getByRole("radio", { name: /OpenAI key/ }));
    expect(await screen.findByText(/About \$0\.42 with this key/)).toBeTruthy();
    expect(mocks.generateEstimate).toHaveBeenCalledWith(
      "r1",
      { selection: { kind: "unwritten" }, cascade: "none" },
      { provider: "openai" },
    );

    fireEvent.click(screen.getByRole("button", { name: "Write the docs" }));

    await waitFor(() => expect(mocks.setActiveProvider).toHaveBeenCalledWith("openai", undefined, "r1"));
    // The shared confirm dialog opens; nothing runs until it is confirmed.
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    expect(mocks.generatePages).not.toHaveBeenCalled();
  });
});
