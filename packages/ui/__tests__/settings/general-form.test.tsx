import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { GeneralForm } from "../../src/settings/general-form.js";
import type { RepoSettingsValue } from "@repowise-dev/types/settings";

const baseValue: RepoSettingsValue = {
  name: "demo",
  default_branch: "main",
  exclude_patterns: ["node_modules/"],
};

describe("GeneralForm", () => {
  it("loads value into inputs", () => {
    render(<GeneralForm value={baseValue} onSubmit={vi.fn()} localPath="/srv/demo" />);
    expect(screen.getByLabelText("Repository name")).toHaveValue("demo");
    expect(screen.getByLabelText("Default branch")).toHaveValue("main");
    expect(screen.getByText("/srv/demo")).toBeInTheDocument();
    expect(screen.getByText("node_modules/")).toBeInTheDocument();
  });

  it("disables save until a field changes; calls onSubmit on save", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<GeneralForm value={baseValue} onSubmit={onSubmit} />);
    const save = screen.getByRole("button", { name: /Save changes/i });
    expect(save).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Repository name"), {
      target: { value: "renamed" },
    });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0]![0]).toMatchObject({ name: "renamed" });
  });

  it("renders read-only when onSubmit is omitted (hosted)", () => {
    render(<GeneralForm value={baseValue} disabledHint="Editing not yet available." />);
    expect(screen.getByLabelText("Repository name")).toBeDisabled();
    expect(screen.getByLabelText("Default branch")).toBeDisabled();
    expect(screen.getByRole("button", { name: /Save changes/i })).toBeDisabled();
    expect(screen.getByText("Editing not yet available.")).toBeInTheDocument();
    // Add-pattern input is suppressed in read-only mode.
    expect(screen.queryByLabelText("New excluded pattern")).not.toBeInTheDocument();
  });
});
