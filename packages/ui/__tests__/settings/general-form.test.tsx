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

  it("defaults the style to comprehensive and submits it", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<GeneralForm value={baseValue} onSubmit={onSubmit} />);
    // The comprehensive option is selected by default.
    const comprehensive = screen.getByRole("radio", { name: /Comprehensive/i });
    expect(comprehensive).toHaveAttribute("aria-checked", "true");

    fireEvent.change(screen.getByLabelText("Repository name"), {
      target: { value: "renamed" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save changes/i }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0]![0]).toMatchObject({ wiki_style: "comprehensive" });
  });

  it("selecting a different style enables save and submits the new style", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<GeneralForm value={baseValue} onSubmit={onSubmit} />);
    const save = screen.getByRole("button", { name: /Save changes/i });
    expect(save).toBeDisabled();

    fireEvent.click(screen.getByRole("radio", { name: /Caveman/i }));
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0]![0]).toMatchObject({ wiki_style: "caveman" });
  });

  it("reflects a preset wiki_style from the value", () => {
    render(
      <GeneralForm
        value={{ ...baseValue, wiki_style: "reference" }}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByRole("radio", { name: /Reference/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  describe("autosave", () => {
    it("has no Save button and saves a text field on blur, with a Saved tick", async () => {
      const onSubmit = vi.fn().mockResolvedValue(undefined);
      render(<GeneralForm value={baseValue} onSubmit={onSubmit} autosave />);
      expect(screen.queryByRole("button", { name: /Save changes/i })).not.toBeInTheDocument();

      const name = screen.getByLabelText("Repository name");
      fireEvent.change(name, { target: { value: "renamed" } });
      expect(onSubmit).not.toHaveBeenCalled();
      fireEvent.blur(name);
      await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
      expect(onSubmit.mock.calls[0]![0]).toMatchObject({ name: "renamed", default_branch: "main" });
      expect(await screen.findByText("Saved")).toBeInTheDocument();
    });

    it("does not save an unchanged or emptied text field", () => {
      const onSubmit = vi.fn().mockResolvedValue(undefined);
      render(<GeneralForm value={baseValue} onSubmit={onSubmit} autosave />);
      const branch = screen.getByLabelText("Default branch");
      fireEvent.blur(branch);
      fireEvent.change(branch, { target: { value: "  " } });
      fireEvent.keyDown(branch, { key: "Enter" });
      expect(onSubmit).not.toHaveBeenCalled();
      expect(branch).toHaveValue("main");
    });

    it("saves a style on click and rolls it back when the write fails", async () => {
      const onSubmit = vi.fn().mockRejectedValue(new Error("nope"));
      render(<GeneralForm value={baseValue} onSubmit={onSubmit} autosave />);
      fireEvent.click(screen.getByRole("radio", { name: /Caveman/i }));
      await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
      expect(onSubmit.mock.calls[0]![0]).toMatchObject({ wiki_style: "caveman" });
      await waitFor(() =>
        expect(screen.getByRole("radio", { name: /Comprehensive/i })).toHaveAttribute("aria-checked", "true"),
      );
      expect(screen.getByText("Could not save")).toBeInTheDocument();
    });

    it("keeps a superseded write that succeeded when a newer one fails", async () => {
      let releaseFirst: () => void = () => {};
      const onSubmit = vi
        .fn()
        .mockImplementationOnce(() => new Promise<void>((r) => (releaseFirst = r)))
        .mockRejectedValueOnce(new Error("nope"));
      render(<GeneralForm value={baseValue} onSubmit={onSubmit} autosave />);
      fireEvent.click(screen.getByRole("radio", { name: /Caveman/i }));
      fireEvent.click(screen.getByRole("button", { name: "+ vendor/" }));
      await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2));
      releaseFirst();
      await waitFor(() => expect(screen.getByText("Could not save")).toBeInTheDocument());
      // The style write landed; only the path write rolls back.
      expect(screen.getByRole("radio", { name: /Caveman/i })).toHaveAttribute("aria-checked", "true");
      expect(screen.queryByText("vendor/")).not.toBeInTheDocument();
    });

    it("saves an excluded path as soon as it is added", async () => {
      const onSubmit = vi.fn().mockResolvedValue(undefined);
      render(<GeneralForm value={baseValue} onSubmit={onSubmit} autosave />);
      fireEvent.click(screen.getByRole("button", { name: "+ vendor/" }));
      await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
      expect(onSubmit.mock.calls[0]![0].exclude_patterns).toEqual(["node_modules/", "vendor/"]);
    });
  });
});
