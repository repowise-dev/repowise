import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  RegenerateButton,
  GenerateConfirmDialog,
} from "../../src/wiki/regenerate-button.js";

describe("RegenerateButton", () => {
  it("renders the regenerate button and fires onRegenerate on click", () => {
    const onRegenerate = vi.fn();
    render(<RegenerateButton onRegenerate={onRegenerate} />);
    fireEvent.click(screen.getByRole("button", { name: /regenerate/i }));
    expect(onRegenerate).toHaveBeenCalledTimes(1);
  });

  it("renders a compact inline trigger and still fires onRegenerate", () => {
    const onRegenerate = vi.fn();
    render(<RegenerateButton mode="write" inline onRegenerate={onRegenerate} />);
    fireEvent.click(screen.getByRole("button", { name: /Write this page with AI/ }));
    expect(onRegenerate).toHaveBeenCalledTimes(1);
  });

  it("disables the button while loading", () => {
    render(<RegenerateButton onRegenerate={vi.fn()} isLoading />);
    expect(screen.getByRole("button", { name: /regenerate/i })).toBeDisabled();
  });

  it("renders the jobSlot inside the dialog when in progress", () => {
    render(
      <RegenerateButton
        onRegenerate={vi.fn()}
        isInProgress
        jobSlot={<div data-testid="job-progress">progress</div>}
      />,
    );
    expect(screen.getByTestId("job-progress")).toBeInTheDocument();
    expect(screen.getByText(/regenerating page/i)).toBeInTheDocument();
  });
});

describe("GenerateConfirmDialog (bulk overrides)", () => {
  const base = {
    open: true,
    onOpenChange: vi.fn(),
    cascade: "none" as const,
    onCascadeChange: vi.fn(),
    onConfirm: vi.fn(),
  };

  it("uses the title, selection-scoped cascade wording, and confirm label overrides", () => {
    render(
      <GenerateConfirmDialog
        {...base}
        title="Write documentation with AI"
        cascadeScope="selection"
        description={<>Write the selected pages with your model.</>}
        confirmLabel="Write 12 pages"
        estimate={{ totalPages: 12, costText: "$0.10 to $0.20" }}
      />,
    );
    expect(screen.getByText("Write documentation with AI")).toBeInTheDocument();
    expect(screen.getByText("Just the selected pages")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Write 12 pages/ }),
    ).toBeInTheDocument();
  });

  it("routes to settings when no provider is configured", () => {
    render(
      <GenerateConfirmDialog
        {...base}
        noProvider
        settingsHref="/repos/r1/settings#provider"
      />,
    );
    const link = screen.getByRole("link", { name: /Add a provider key/ });
    expect(link).toHaveAttribute("href", "/repos/r1/settings#provider");
  });
});
