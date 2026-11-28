// @vitest-environment jsdom

/**
 * The rewrite button's failure path: a dropped connection can hide a rewrite
 * the API already stored, so the view reloads before it reports a failure,
 * and a failure it does report never reads as a bare status line.
 */

import React from "react";
import { SWRConfig } from "swr";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@repowise-dev/api-client";

const mocks = vi.hoisted(() => ({
  getFirstLook: vi.fn(),
  rewriteFirstLook: vi.fn(),
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("@/lib/api/reports", () => ({
  getFirstLook: mocks.getFirstLook,
  rewriteFirstLook: mocks.rewriteFirstLook,
  getFirstLookPrompt: vi.fn(),
  markFirstLookItem: vi.fn(),
  firstLookMarkdownPath: () => "/export.md",
}));
vi.mock("sonner", () => ({ toast: mocks.toast }));
vi.mock("@repowise-dev/ui/report/first-look", () => ({
  FirstLookReport: (props: {
    report: { prose_source: { writer: string } };
    onRewrite: () => void;
  }) => (
    <div>
      <span data-testid="writer">{props.report.prose_source.writer}</span>
      <button onClick={props.onRewrite}>rewrite</button>
    </div>
  ),
}));

import { FirstLookView } from "./first-look-view";

function page(writer: string, writtenAt: string | null) {
  return {
    report: { prose_source: { writer, written_at: writtenAt, cost_usd: null } },
    can_rewrite: true,
    rewrite_cost_usd: 0.0033,
  };
}

function renderView() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <FirstLookView repoId="repo-1" />
    </SWRConfig>,
  );
}

async function clickRewrite() {
  fireEvent.click(await screen.findByText("rewrite"));
}

describe("FirstLookView rewrite", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });
  afterEach(cleanup);

  it("shows a rewrite the API stored even though the proxy dropped the reply", async () => {
    mocks.getFirstLook
      .mockResolvedValueOnce(page("template", null))
      .mockResolvedValue(page("claude-haiku", "2026-11-22T10:00:00Z"));
    mocks.rewriteFirstLook.mockRejectedValue(new ApiClientError(500, "Internal Server Error"));

    renderView();
    await clickRewrite();

    await waitFor(() => expect(screen.getByTestId("writer").textContent).toBe("claude-haiku"));
    expect(mocks.toast.error).not.toHaveBeenCalled();
  });

  it("names the failure in words, not a status line, when nothing was stored", async () => {
    mocks.getFirstLook.mockResolvedValue(page("template", null));
    mocks.rewriteFirstLook.mockRejectedValue(new ApiClientError(500, "Internal Server Error"));

    renderView();
    await clickRewrite();

    await waitFor(() => expect(mocks.toast.error).toHaveBeenCalledTimes(1));
    const message = mocks.toast.error.mock.calls[0][0] as string;
    expect(message).not.toMatch(/500|Internal Server Error/);
    expect(message).toBe("Couldn't write the report");
  });

  it("passes the server's words through for a refused rewrite", async () => {
    mocks.getFirstLook.mockResolvedValue(page("template", null));
    mocks.rewriteFirstLook.mockRejectedValue(
      new ApiClientError(409, "No model provider is configured for this repository"),
    );

    renderView();
    await clickRewrite();

    await waitFor(() =>
      expect(mocks.toast.error).toHaveBeenCalledWith("No model provider is configured for this repository"),
    );
  });
});
