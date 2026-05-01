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
    fireEvent.click(screen.getByText("claude-sonnet-4-6"));
    expect(onActivate).toHaveBeenCalledWith("anthropic", "claude-sonnet-4-6");
  });

  it("shows Add-key affordance for unconfigured providers", () => {
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
    expect(screen.getByText("Add key")).toBeInTheDocument();
  });
});
