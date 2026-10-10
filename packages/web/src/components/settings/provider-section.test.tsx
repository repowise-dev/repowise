// @vitest-environment jsdom

import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getProviders: vi.fn(),
  getProvider: vi.fn(() => "gemini"),
  getEmbedder: vi.fn(() => "mock"),
}));

vi.mock("@/lib/api/providers", () => ({ getProviders: mocks.getProviders }));
vi.mock("@/lib/config", () => ({
  config: {
    getProvider: mocks.getProvider,
    getModel: () => "",
    getEmbedder: mocks.getEmbedder,
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
    expect(await screen.findByText(/Could not load the provider list/)).toBeTruthy();
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
      flag_only_providers: ["mock"],
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

  it("takes the model placeholder from the server catalog", async () => {
    // The page keeps no table of its own; the catalog is derived from the
    // provider specs, so the server's default is the one to show.
    mocks.getProviders.mockResolvedValue({
      active: { provider: "gemini", model: null },
      providers: [{ id: "gemini", name: "Google Gemini", default_model: "gemini-4-pro" }],
    });

    render(<ProviderSection />);

    const model = await screen.findByLabelText<HTMLInputElement>("Model");
    await waitFor(() => expect(model.placeholder).toBe("gemini-4-pro"));
  });

  it("falls back to a generic placeholder when the catalog has no default", async () => {
    mocks.getProviders.mockResolvedValue({
      active: { provider: "gemini", model: null },
      providers: [{ id: "gemini", name: "Google Gemini", default_model: null }],
    });

    render(<ProviderSection />);

    await waitFor(() => expect(mocks.getProviders).toHaveBeenCalledOnce());
    expect(screen.getByLabelText<HTMLInputElement>("Model").placeholder).toBe("model name");
  });

  it("shows the env vars and setup hint the catalog carries", async () => {
    mocks.getProviders.mockResolvedValue({
      active: { provider: "gemini", model: null },
      providers: [
        {
          id: "gemini",
          name: "Google Gemini",
          default_model: "gemini-3.5-flash-lite",
          env_vars: ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
          setup_hint: "pip install google-genai",
        },
      ],
    });

    render(<ProviderSection />);

    expect(await screen.findByText("pip install google-genai")).toBeTruthy();
    expect(screen.getByText(/GEMINI_API_KEY/)).toBeTruthy();
  });
});

describe("ProviderSection server defaults", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.hasPointerCapture = vi.fn(() => false);
    Element.prototype.releasePointerCapture = vi.fn();
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("shows the server's provider and embedder when nothing is saved", async () => {
    mocks.getProvider.mockReturnValue("");
    mocks.getEmbedder.mockReturnValue("");
    mocks.getProviders.mockResolvedValue({
      active: { provider: "openai", model: null, embedder: "gemini" },
      providers: [{ id: "openai", name: "OpenAI", default_model: "gpt-5.6-luna" }],
      embedders: [
        { id: "gemini", env_vars: ["GEMINI_API_KEY"], semantic: true },
        { id: "mock", env_vars: [], semantic: false },
      ],
    });

    render(<ProviderSection />);

    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Provider" }).textContent).toContain("openai"),
    );
    expect(screen.getByRole("combobox", { name: "Embedder" }).textContent).toContain("gemini");
    expect(screen.getByText(/GEMINI_API_KEY/)).toBeTruthy();
  });

  it("offers the server's embedders and says when one carries no signal", async () => {
    mocks.getProvider.mockReturnValue("openai");
    mocks.getEmbedder.mockReturnValue("mock");
    mocks.getProviders.mockResolvedValue({
      active: { provider: "openai", model: null, embedder: "mock" },
      providers: [],
      embedders: [
        { id: "voyage", env_vars: [], semantic: true },
        { id: "mock", env_vars: [], semantic: false },
      ],
    });

    render(<ProviderSection />);

    expect(await screen.findByText(/Semantic search is off/)).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("combobox", { name: "Embedder" }), {
      key: "Enter",
      code: "Enter",
    });
    await waitFor(() => expect(screen.queryAllByRole("option").length).toBeGreaterThan(0));
    expect(screen.queryAllByRole("option").map((o) => o.textContent)).toEqual(["voyage", "mock"]);
  });
});
