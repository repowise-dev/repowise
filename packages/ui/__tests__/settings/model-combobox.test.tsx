import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ModelCombobox } from "../../src/settings/model-combobox.js";

const MODELS = [{ id: "gpt-6" }, { id: "gpt-5.4" }, { id: "gpt-5.4-mini" }];

describe("ModelCombobox", () => {
  it("opens lazily, filters, and moves with the arrow keys", () => {
    const onOpen = vi.fn();
    const onSelect = vi.fn();
    render(
      <ModelCombobox value="gpt-6" onSelect={onSelect} models={MODELS} onOpen={onOpen} ariaLabel="Model" />,
    );
    const input = screen.getByRole("combobox", { name: "Model" });
    expect(input).toHaveAttribute("aria-expanded", "false");

    fireEvent.focus(input);
    expect(onOpen).toHaveBeenCalledTimes(1);
    fireEvent.change(input, { target: { value: "5.4" } });
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([
      "gpt-5.4",
      "gpt-5.4-mini",
      'Use "5.4"',
    ]);

    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input.getAttribute("aria-activedescendant")).toBe(
      screen.getAllByRole("option")[1]!.id,
    );
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("gpt-5.4-mini");
    expect(input).toHaveAttribute("aria-expanded", "false");
  });

  it("closes on Escape without selecting", () => {
    const onSelect = vi.fn();
    render(<ModelCombobox value="" onSelect={onSelect} models={MODELS} ariaLabel="Model" />);
    const input = screen.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.keyDown(input, { key: "Escape" });
    expect(input).toHaveAttribute("aria-expanded", "false");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("marks the default model", () => {
    render(
      <ModelCombobox inline value="" onSelect={vi.fn()} models={MODELS} defaultModel="gpt-6" />,
    );
    expect(screen.getByRole("option", { name: /gpt-6/ })).toHaveTextContent("default");
  });
});
