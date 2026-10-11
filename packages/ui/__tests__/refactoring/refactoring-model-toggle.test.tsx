import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RefactoringModelToggle } from "../../src/refactoring/refactoring-settings-card";

const LABEL = "Use your configured model to name helpers and draft code";

describe("RefactoringModelToggle", () => {
  it("names the provider and model chat resolves", () => {
    render(
      <RefactoringModelToggle
        value={{ enabled: false, provider: "anthropic", model: "claude-test" }}
        onToggle={vi.fn()}
      />,
    );
    expect(screen.getByRole("switch", { name: LABEL })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText("anthropic")).toBeInTheDocument();
    expect(screen.getByText("claude-test")).toBeInTheDocument();
    expect(screen.queryByText("No model configured.", { exact: false })).toBeNull();
  });

  it("links to setup when no model is configured", () => {
    render(
      <RefactoringModelToggle
        value={{ enabled: false, provider: null, model: null }}
        onToggle={vi.fn()}
        setupHref="/repos/r1/settings#provider"
      />,
    );
    expect(screen.getByText("No model configured.", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Set one up" })).toHaveAttribute(
      "href",
      "/repos/r1/settings#provider",
    );
  });

  it("writes the switch and rolls back when the write fails", async () => {
    const onToggle = vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error("x"));
    render(
      <RefactoringModelToggle
        value={{ enabled: false, provider: "anthropic", model: null }}
        onToggle={onToggle}
      />,
    );
    const toggle = screen.getByRole("switch", { name: LABEL });

    fireEvent.click(toggle);
    expect(onToggle).toHaveBeenLastCalledWith(true);
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    await waitFor(() => expect(toggle).not.toBeDisabled());

    fireEvent.click(toggle);
    expect(onToggle).toHaveBeenLastCalledWith(false);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(toggle).toHaveAttribute("aria-checked", "true");
  });
});
