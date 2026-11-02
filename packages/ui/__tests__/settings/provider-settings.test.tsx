import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import {
  ProviderSettings,
  type ProviderOption,
} from "../../src/settings/provider-settings.js";

const OPENAI: ProviderOption = {
  id: "openai",
  name: "OpenAI",
  models: ["gpt-5.6-luna", "gpt-5.4"],
  default_model: "gpt-5.6-luna",
  configured: false,
  accepts_base_url: true,
  local_server: true,
};
const ANTHROPIC: ProviderOption = {
  id: "anthropic",
  name: "Anthropic",
  models: ["claude-haiku-5-5"],
  default_model: "claude-haiku-5-5",
  configured: true,
};

function renderSettings(overrides: Partial<Parameters<typeof ProviderSettings>[0]> = {}) {
  const props = {
    providers: [OPENAI, ANTHROPIC],
    active: {
      provider: "openai",
      model: "gpt-5.6-luna",
      base_url: null,
      provenance: { provider: "config.yaml", model: "env:REPOWISE_MODEL", base_url: null },
    },
    onAddKey: vi.fn(),
    onRemoveKey: vi.fn(),
    onSetActive: vi.fn(),
    onValidate: vi.fn(),
    onLoadModels: vi.fn(),
    ...overrides,
  };
  render(<ProviderSettings {...props} />);
  return props;
}

describe("ProviderSettings form", () => {
  it("names where the model came from", () => {
    renderSettings();
    expect(
      screen.getByText(
        "From the REPOWISE_MODEL environment variable. Saving here takes precedence.",
      ),
    ).toBeInTheDocument();
  });

  it("fills a local server URL and tests it before saving", async () => {
    const onLoadModels = vi.fn().mockResolvedValue({
      models: [{ id: "qwen3-coder" }, { id: "llama-4" }],
      source: "live",
      default_model: "gpt-5.6-luna",
    });
    renderSettings({ onLoadModels });

    fireEvent.click(screen.getByRole("button", { name: "LM Studio" }));
    expect(screen.getByLabelText("Base URL")).toHaveValue("http://localhost:1234/v1");
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Connected. 2 models listed."),
    );
    expect(onLoadModels).toHaveBeenCalledWith("openai", {
      baseUrl: "http://localhost:1234/v1",
      refresh: true,
    });
  });

  it("shows why a connection test failed", async () => {
    const onLoadModels = vi.fn().mockResolvedValue({
      models: [],
      source: "catalog",
      default_model: "gpt-5.6-luna",
      error: "Could not reach http://localhost:1234/v1. Is LM Studio's server running?",
    });
    renderSettings({ onLoadModels });

    fireEvent.change(screen.getByLabelText("Base URL"), {
      target: { value: "http://localhost:1234/v1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Is LM Studio's server running?"),
    );
  });

  it("warns that a model change regenerates docs, then saves model and URL", async () => {
    const props = renderSettings();
    expect(screen.queryByText(/regenerated with the new model/)).toBeNull();

    const model = screen.getByRole("combobox", { name: "OpenAI Model" });
    fireEvent.change(model, { target: { value: "gpt-6" } });
    fireEvent.keyDown(model, { key: "Enter" });
    fireEvent.change(screen.getByLabelText("Base URL"), {
      target: { value: "http://localhost:1234/v1" },
    });

    expect(screen.getByText(/regenerated with the new model/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(props.onSetActive).toHaveBeenCalledWith("openai", "gpt-6", {
        baseUrl: "http://localhost:1234/v1",
      }),
    );
  });

  it("rejects a URL without http(s) and keeps Save disabled", () => {
    renderSettings();
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "localhost:1234" } });
    expect(
      screen.getByText("Enter a URL that starts with http:// or https://."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("opens the form on Use and writes nothing until Save", async () => {
    const props = renderSettings();
    fireEvent.click(screen.getByRole("button", { name: "Use Anthropic" }));
    expect(props.onSetActive).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Use Anthropic" }));
    await waitFor(() =>
      expect(props.onSetActive).toHaveBeenCalledWith("anthropic", "claude-haiku-5-5", undefined),
    );
  });

  it("lets a keyless local-server provider be picked", () => {
    renderSettings({
      active: { provider: "anthropic", model: "claude-haiku-5-5" },
    });
    expect(screen.getByRole("button", { name: "Use OpenAI" })).toBeEnabled();
  });

  it("offers to trust an unconfirmed config.yaml URL by saving it again", async () => {
    const props = renderSettings({
      active: {
        provider: "openai",
        model: "gpt-5.6-luna",
        base_url: "https://gateway.example.com/v1",
        base_url_unconfirmed: true,
        provenance: { provider: "config.yaml", model: "default", base_url: "config.yaml" },
      },
    });

    expect(screen.getByRole("alert")).toHaveTextContent("https://gateway.example.com/v1");
    fireEvent.click(screen.getByRole("button", { name: "Trust this URL" }));
    await waitFor(() =>
      expect(props.onSetActive).toHaveBeenCalledWith("openai", undefined, {
        baseUrl: "https://gateway.example.com/v1",
      }),
    );
  });

  it("fetches the list again after a key is added", async () => {
    const onLoadModels = vi.fn().mockResolvedValue({
      models: [{ id: "gpt-6" }],
      source: "live",
      default_model: "gpt-5.6-luna",
    });
    const base = {
      providers: [OPENAI],
      active: { provider: "openai", model: "gpt-5.6-luna" },
      onAddKey: vi.fn(),
      onRemoveKey: vi.fn(),
      onSetActive: vi.fn(),
      onValidate: vi.fn(),
      onLoadModels,
    };
    const { rerender } = render(<ProviderSettings {...base} />);
    const model = () => screen.getByRole("combobox", { name: "OpenAI Model" });
    fireEvent.focus(model());
    await waitFor(() => expect(onLoadModels).toHaveBeenCalledTimes(1));
    fireEvent.blur(model());

    rerender(<ProviderSettings {...base} providers={[{ ...OPENAI, configured: true }]} />);
    fireEvent.focus(model());
    await waitFor(() => expect(onLoadModels).toHaveBeenCalledTimes(2));
  });

  it("keeps the drafts when a save fails", async () => {
    const onSetActive = vi.fn().mockRejectedValue(new Error("nope"));
    renderSettings({ onSetActive });

    const model = screen.getByRole("combobox", { name: "OpenAI Model" });
    fireEvent.change(model, { target: { value: "gpt-6" } });
    fireEvent.keyDown(model, { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved");
    expect(model).toHaveValue("gpt-6");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("takes its row copy from labels", () => {
    renderSettings({ labels: { test: "Probar", active: "Activo" } });
    expect(screen.getByText("Activo")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test Anthropic connection" })).toHaveTextContent("Probar");
  });
});
