// @vitest-environment jsdom

import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getProviders: vi.fn(),
  getProvider: vi.fn(() => "gemini"),
}));

vi.mock("@/lib/api/providers", () => ({ getProviders: mocks.getProviders }));
vi.mock("@/lib/config", () => ({
  config: {
    getProvider: mocks.getProvider,
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

describe("ProviderSection provider catalog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getProvider.mockReturnValue("gemini");
    // Radix needs these to open its listbox; jsdom implements none of them.
    Element.prototype.hasPointerCapture = vi.fn(() => false);
    Element.prototype.releasePointerCapture = vi.fn();
    Element.prototype.scrollIntoView = vi.fn();
  });

  /** Opens the provider listbox and returns the option labels it offers. */
  async function openProviderOptions(): Promise<string[]> {
    fireEvent.keyDown(screen.getByRole("combobox", { name: "Provider" }), {
      key: "Enter",
      code: "Enter",
    });
    await waitFor(() => expect(screen.queryAllByRole("option").length).toBeGreaterThan(0));
    return screen.queryAllByRole("option").map((option) => option.textContent ?? "");
  }

  it("still offers a flag-only provider the catalog leaves out", async () => {
    // `mock` is registerable and keyless but never appears in the server
    // catalog, so rendering the catalog verbatim took away a choice the page
    // has always offered -- silently, since the user is on gemini and the
    // trigger still looks right.
    mocks.getProviders.mockResolvedValue({
      active: { provider: "gemini", model: null },
      providers: [
        { id: "gemini", name: "Google Gemini", default_model: "gemini-3.5-flash-lite" },
        { id: "codex_cli", name: "Codex CLI", default_model: "codex_cli/gpt-5.6-luna" },
      ],
    });

    render(<ProviderSection />);
    await waitFor(() => expect(mocks.getProviders).toHaveBeenCalledOnce());

    expect(await openProviderOptions()).toEqual(["gemini", "codex_cli", "mock"]);
  });

  it("takes the model placeholder from a provider it has no local entry for", async () => {
    // codex_cli is the real case: it reached the server catalog and was never
    // added to this file's tables, so before the catalog was read here it was
    // unselectable and had no placeholder.
    mocks.getProvider.mockReturnValue("codex_cli");
    mocks.getProviders.mockResolvedValue({
      active: { provider: "codex_cli", model: null },
      providers: [
        { id: "codex_cli", name: "Codex CLI", default_model: "codex_cli/gpt-5.6-luna" },
        { id: "openrouter", name: "OpenRouter", default_model: "openrouter/auto" },
      ],
    });

    render(<ProviderSection />);

    const model = await screen.findByLabelText<HTMLInputElement>("Model");
    await waitFor(() => expect(model.placeholder).toBe("codex_cli/gpt-5.6-luna"));
  });

  it("keeps the selected provider selectable when the catalog omits it", async () => {
    // `mock` is flag-only and is deliberately absent from the server catalog,
    // so rendering the catalog verbatim would leave a user who has it saved
    // staring at a picker with no matching option and a blank trigger.
    mocks.getProvider.mockReturnValue("mock");
    mocks.getProviders.mockResolvedValue({
      active: { provider: "gemini", model: null },
      providers: [
        { id: "gemini", name: "Google Gemini", default_model: "gemini-3.5-flash-lite" },
        { id: "codex_cli", name: "Codex CLI", default_model: "codex_cli/gpt-5.6-luna" },
      ],
    });

    render(<ProviderSection />);

    await waitFor(() => expect(mocks.getProviders).toHaveBeenCalledOnce());
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: /provider/i }).textContent).toContain("mock"),
    );
  });

  it("lets the server's default model beat a stale built-in placeholder", async () => {
    // The local table is a hardcoded guess that has already drifted once. When
    // the server reports a different default, the server is right.
    mocks.getProviders.mockResolvedValue({
      active: { provider: "gemini", model: null },
      providers: [{ id: "gemini", name: "Google Gemini", default_model: "gemini-4-pro" }],
    });

    render(<ProviderSection />);

    const model = await screen.findByLabelText<HTMLInputElement>("Model");
    await waitFor(() => expect(model.placeholder).toBe("gemini-4-pro"));
  });

  it("falls back to the built-in placeholder when the catalog has no default", async () => {
    mocks.getProviders.mockResolvedValue({
      active: { provider: "gemini", model: null },
      providers: [{ id: "gemini", name: "Google Gemini", default_model: null }],
    });

    render(<ProviderSection />);

    await waitFor(() => expect(mocks.getProviders).toHaveBeenCalledOnce());
    expect(screen.getByLabelText<HTMLInputElement>("Model").placeholder).toBe(
      "gemini-3.5-flash-lite",
    );
  });
});
