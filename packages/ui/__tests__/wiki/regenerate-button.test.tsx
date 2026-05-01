import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RegenerateButton } from "../../src/wiki/regenerate-button.js";

describe("RegenerateButton", () => {
  it("renders the regenerate button and fires onRegenerate on click", () => {
    const onRegenerate = vi.fn();
    render(<RegenerateButton onRegenerate={onRegenerate} />);
    fireEvent.click(screen.getByRole("button", { name: /regenerate/i }));
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
