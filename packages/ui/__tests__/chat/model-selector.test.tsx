import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ModelSelector } from "../../src/chat/model-selector.js";

const PROVIDERS = [
  {
    id: "anthropic",
    name: "Anthropic",
    models: ["claude-opus-4-7", "claude-sonnet-4-6"],
    default_model: "claude-opus-4-7",
    configured: true,
  },
  {
    id: "openai",
    name: "OpenAI",
    models: ["gpt-5"],
    default_model: "gpt-5",
    configured: false,
  },
];

describe("ModelSelector shell", () => {
  it("shows the active provider/model in the trigger label", () => {
    render(
      <ModelSelector
        providers={PROVIDERS}
        activeProvider="anthropic"
        activeModel="claude-opus-4-7"
        onActivate={vi.fn()}
        onSaveKey={vi.fn()}
      />,
    );
    expect(
      screen.getByText("Anthropic · claude-opus-4-7"),
    ).toBeInTheDocument();
  });

  it("calls onActivate with provider + model when a configured model is clicked", async () => {
    const onActivate = vi.fn();
    render(
      <ModelSelector
        providers={PROVIDERS}
        activeProvider={null}
        activeModel={null}
        onActivate={onActivate}
        onSaveKey={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText("Select model"));
    fireEvent.mouseDown(screen.getByRole("option", { name: /claude-sonnet-4-6/ }));
    expect(onActivate).toHaveBeenCalledWith("anthropic", "claude-sonnet-4-6");
  });

  it("takes a typed model id and marks the repo default", () => {
    const onActivate = vi.fn();
    const onBrowse = vi.fn();
    render(
      <ModelSelector
        providers={PROVIDERS}
        activeProvider="anthropic"
        activeModel="claude-opus-4-7"
        defaultProvider="anthropic"
        defaultModel="claude-opus-4-7"
        onActivate={onActivate}
        onBrowse={onBrowse}
        models={[{ id: "claude-opus-4-7" }, { id: "claude-next-1" }]}
      />,
    );
    fireEvent.click(screen.getByText("Anthropic · claude-opus-4-7"));
    expect(onBrowse).toHaveBeenCalledWith("anthropic");
    expect(screen.getByRole("option", { name: /claude-next-1/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /claude-opus-4-7/ })).toHaveTextContent("repo default");

    const search = screen.getByRole("combobox");
    fireEvent.change(search, { target: { value: "my-finetune" } });
    fireEvent.keyDown(search, { key: "Enter" });
    expect(onActivate).toHaveBeenCalledWith("anthropic", "my-finetune");
  });

  it("routes API-key configuration to Settings", () => {
    render(
      <ModelSelector
        providers={PROVIDERS}
        activeProvider="anthropic"
        activeModel="claude-opus-4-7"
        onActivate={vi.fn()}
        onSaveKey={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText("Anthropic · claude-opus-4-7"));
    expect(screen.getByRole("link", { name: "Configure in Settings" })).toHaveAttribute("href", "/settings");
    expect(screen.queryByText("Add key")).toBeNull();
  });

  it("opens above the composer with neutral model chrome", () => {
    render(<ModelSelector providers={PROVIDERS} activeProvider="anthropic" activeModel="claude-opus-4-7" onActivate={vi.fn()} />);
    fireEvent.click(screen.getByText("Anthropic · claude-opus-4-7"));
    const panel = screen.getByRole("dialog", { name: "Choose conversation model" });
    expect(panel.className).toContain("bottom-full");
    expect(panel.className).toContain("left-0");
    const trigger = screen.getByRole("button", { name: "Anthropic · claude-opus-4-7" });
    expect(trigger.className).not.toContain("accent-fill");
    expect(screen.getByRole("option", { name: /claude-opus-4-7/ })).toHaveAttribute("aria-selected", "true");
  });

  it("closes on Escape and restores focus to the model trigger", () => {
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => { callback(0); return 1; });
    render(<ModelSelector providers={PROVIDERS} activeProvider="anthropic" activeModel="claude-opus-4-7" onActivate={vi.fn()} />);
    const trigger = screen.getByRole("button", { name: "Anthropic · claude-opus-4-7" });
    fireEvent.click(trigger);
    expect(screen.getByRole("dialog", { name: "Choose conversation model" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Choose conversation model" })).toBeNull();
    expect(trigger).toHaveFocus();
  });
});
