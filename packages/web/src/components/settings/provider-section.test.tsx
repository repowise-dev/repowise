// @vitest-environment jsdom

import React from "react";
import { SWRConfig } from "swr";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getProviders: vi.fn(),
  setActiveProvider: vi.fn(),
  getProviderModels: vi.fn(),
}));

vi.mock("@/lib/api/providers", () => ({
  getProviders: mocks.getProviders,
  setActiveProvider: mocks.setActiveProvider,
  getProviderModels: mocks.getProviderModels,
  addProviderKey: vi.fn(),
  removeProviderKey: vi.fn(),
  validateProviderKey: vi.fn(),
}));

import { ProviderSection } from "./provider-section";

const STATUS = {
  active: {
    provider: "openai",
    model: "gpt-5.6-luna",
    base_url: null,
    provenance: { provider: "global", model: "default", base_url: null },
    embedder: "gemini",
  },
  providers: [
    {
      id: "openai",
      name: "OpenAI",
      models: ["gpt-5.6-luna"],
      default_model: "gpt-5.6-luna",
      configured: true,
      accepts_base_url: true,
      local_server: true,
    },
  ],
  embedders: [{ id: "gemini", env_vars: ["GEMINI_API_KEY"], semantic: true }],
};

function renderSection() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <ProviderSection />
    </SWRConfig>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ProviderSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getProviders.mockResolvedValue(STATUS);
  });

  it("edits the server default through the real form, with no repo", async () => {
    mocks.setActiveProvider.mockResolvedValue(STATUS);
    renderSection();

    const url = await screen.findByLabelText("Base URL");
    expect(mocks.getProviders).toHaveBeenCalledWith(undefined);
    fireEvent.change(url, { target: { value: "http://localhost:1234/v1" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(mocks.setActiveProvider).toHaveBeenCalledWith("openai", "gpt-5.6-luna", undefined, {
        baseUrl: "http://localhost:1234/v1",
      }),
    );
  });

  it("asks for the live list only when the model list opens", async () => {
    mocks.getProviderModels.mockResolvedValue({
      models: [{ id: "gpt-6" }],
      source: "live",
      default_model: "gpt-5.6-luna",
    });
    renderSection();

    const model = await screen.findByRole("combobox", { name: "OpenAI Model" });
    expect(mocks.getProviderModels).not.toHaveBeenCalled();
    fireEvent.focus(model);

    expect(await screen.findByRole("option", { name: /gpt-6/ })).toBeTruthy();
    expect(mocks.getProviderModels).toHaveBeenCalledWith("openai", { refresh: false });
  });

  it("shows the embedder the server runs with, read-only", async () => {
    renderSection();
    expect(await screen.findByText("gemini")).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "Embedder" })).toBeNull();
  });
});
