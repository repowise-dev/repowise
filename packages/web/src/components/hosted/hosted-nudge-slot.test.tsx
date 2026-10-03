// @vitest-environment jsdom

import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  identity: vi.fn(),
  publishRepo: vi.fn(),
}));

vi.mock("@/lib/hooks/use-hosted-identity", () => ({
  useHostedIdentity: () => ({ identity: mocks.identity() }),
}));
vi.mock("@/lib/api/platform", () => ({ publishRepo: mocks.publishRepo }));

import { config } from "@/lib/config";
import { HostedNudgeSlot } from "./hosted-nudge-slot";

const SHARE = "Share this page with your team: a public link, no install needed.";
const DOCS = "Want AI-written docs without an API key? repowise.dev includes the model.";

beforeEach(() => {
  window.localStorage.clear();
  mocks.identity.mockReturnValue({ anon_id: "a1", signed_in: false, hints_enabled: true });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("HostedNudgeSlot", () => {
  it("shows one tip, linking to its moment with the anonymous id", async () => {
    render(<HostedNudgeSlot candidates={["docs", "share"]} repoId="r1" />);
    expect(await screen.findByText(DOCS)).toBeTruthy();
    expect(screen.queryByText(SHARE)).toBeNull();
    expect(screen.getAllByRole("link", { name: "See how" })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "See how" }).getAttribute("href")).toBe(
      "https://repowise.dev/hosted?src=local_web_docs&aid=a1#keys",
    );
  });

  it("shows nothing for a signed-in user", async () => {
    mocks.identity.mockReturnValue({ anon_id: null, signed_in: true, hints_enabled: true });
    render(<HostedNudgeSlot candidates={["share"]} repoId="r1" />);
    await Promise.resolve();
    expect(screen.queryByText(SHARE)).toBeNull();
  });

  it("shows nothing when the local tips switch is off", async () => {
    config.setHostedTipsHidden(true);
    render(<HostedNudgeSlot candidates={["share"]} repoId="r1" />);
    await Promise.resolve();
    expect(screen.queryByText(SHARE)).toBeNull();
  });

  it("stays dismissed on the next visit", async () => {
    const first = render(<HostedNudgeSlot candidates={["share"]} repoId="r1" />);
    fireEvent.click(
      await screen.findByRole("button", { name: /dismiss this repowise\.dev tip/i }),
    );
    expect(screen.queryByText(SHARE)).toBeNull();
    first.unmount();

    render(<HostedNudgeSlot candidates={["share"]} repoId="r1" />);
    await Promise.resolve();
    expect(screen.queryByText(SHARE)).toBeNull();
  });

  it("runs the one-click publish from its free action on a repo page", async () => {
    mocks.publishRepo.mockResolvedValue({
      outcome: "signed_out",
      message: "Sign in to repowise.dev first: repowise login",
      url: null,
      details: [],
      open_url: null,
      repo: "o/n",
    });
    render(<HostedNudgeSlot candidates={["share"]} repoId="r1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Publish it free" }));
    expect(await screen.findByText("Sign in to repowise.dev first: repowise login")).toBeTruthy();
    expect(mocks.publishRepo).toHaveBeenCalledWith("r1");
  });

  it("names the CLI command where no repo is known", async () => {
    render(<HostedNudgeSlot candidates={["mcp"]} />);
    expect(await screen.findByText("repowise publish")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Publish it free" })).toBeNull();
  });
});
