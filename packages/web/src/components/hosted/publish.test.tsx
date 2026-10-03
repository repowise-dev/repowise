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

import { PublishPanel } from "./publish";

const BUTTON = "Publish to repowise.dev";

beforeEach(() => {
  mocks.identity.mockReturnValue({ anon_id: null, signed_in: true, hints_enabled: true });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PublishPanel", () => {
  it("asks a signed-out user to sign in from a terminal instead of offering the button", () => {
    mocks.identity.mockReturnValue({ anon_id: null, signed_in: false, hints_enabled: true });
    render(<PublishPanel repoId="r1" />);
    expect(screen.queryByRole("button", { name: BUTTON })).toBeNull();
    expect(screen.getByText(/Sign in first: run/).textContent).toContain(
      "Sign in first: run repowise login in a terminal.",
    );
    expect(screen.getByText(/Only what's pushed to GitHub is published/)).toBeTruthy();
  });

  it("is busy while publishing, then shows the message, the link and the details", async () => {
    let resolve!: (v: unknown) => void;
    mocks.publishRepo.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<PublishPanel repoId="r1" />);

    fireEvent.click(screen.getByRole("button", { name: BUTTON }));
    const busy = screen.getByRole("button", { name: "Publishing…" }) as HTMLButtonElement;
    expect(busy.disabled).toBe(true);
    expect(mocks.publishRepo).toHaveBeenCalledWith("r1");

    resolve({
      outcome: "published",
      message: "Your repo is being indexed:",
      url: "https://repowise.dev/s/abc/indexing",
      details: ["A hosted index usually takes about 10 minutes."],
      open_url: "https://repowise.dev/s/abc/indexing",
      repo: "o/n",
    });

    expect(await screen.findByText("Your repo is being indexed:")).toBeTruthy();
    const link = screen.getByRole("link", { name: "https://repowise.dev/s/abc/indexing" });
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(screen.getByText("A hosted index usually takes about 10 minutes.")).toBeTruthy();
    expect(screen.getByRole("button", { name: BUTTON })).toBeTruthy();
  });

  it("shows a refusal's message and link", async () => {
    mocks.publishRepo.mockResolvedValue({
      outcome: "needs_app",
      message: "Install the GitHub app first:",
      url: "https://github.com/apps/repowise-app/installations/new",
      details: [],
      open_url: null,
      repo: "o/n",
    });
    render(<PublishPanel repoId="r1" />);
    fireEvent.click(screen.getByRole("button", { name: BUTTON }));

    expect(await screen.findByText("Install the GitHub app first:")).toBeTruthy();
    expect(
      screen.getByRole("link", {
        name: "https://github.com/apps/repowise-app/installations/new",
      }),
    ).toBeTruthy();
  });

  it("states the free limits as a free account's, since the plan is unknown here", () => {
    render(<PublishPanel repoId="r1" />);
    const note = screen.getByText(/Only what's pushed to GitHub is published/).textContent ?? "";
    expect(note).toContain("On a free account, public repos are free (up to 2)");
    expect(note).toContain("free for 10 days, card required");
  });

  it("renders nothing while identity is unknown", () => {
    mocks.identity.mockReturnValue(null);
    const { container } = render(<PublishPanel repoId="r1" />);
    expect(container.textContent).toBe("");
  });
});
