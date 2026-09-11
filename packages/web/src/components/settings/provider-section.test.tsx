// @vitest-environment jsdom

import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getProviders: vi.fn(),
}));

vi.mock("@/lib/api/providers", () => ({ getProviders: mocks.getProviders }));
vi.mock("@/lib/config", () => ({
  config: {
    getProvider: () => "gemini",
    getModel: () => "",
    getEmbedder: () => "mock",
    setProvider: vi.fn(),
    setModel: vi.fn(),
    setEmbedder: vi.fn(),
  },
}));

import { ProviderSection } from "./provider-section";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ProviderSection server provider", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads the active provider through the shared providers client", async () => {
    mocks.getProviders.mockResolvedValue({
      active: { provider: "openai", model: "gpt-5.6-luna" },
      providers: [],
    });

    render(<ProviderSection />);

    await waitFor(() => expect(mocks.getProviders).toHaveBeenCalledOnce());
    expect(
      await screen.findByText(/server itself is currently configured with openai/i),
    ).toBeTruthy();
  });

  it("warns when the provider probe fails instead of swallowing the error", async () => {
    const error = new Error("server unavailable");
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    mocks.getProviders.mockRejectedValue(error);

    render(<ProviderSection />);

    await waitFor(() =>
      expect(warn).toHaveBeenCalledWith(
        "[settings] Could not load the active server provider",
        error,
      ),
    );
    expect(
      screen.getByText("Used when you trigger init or sync from this dashboard."),
    ).toBeTruthy();
  });
});
