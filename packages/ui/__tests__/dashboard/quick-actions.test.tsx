import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QuickActions } from "../../src/dashboard/quick-actions.js";

describe("QuickActions", () => {
  it("renders all three default action buttons + last-synced footer", () => {
    render(
      <QuickActions
        onAction={vi.fn()}
        lastSyncAt="2026-01-01T00:00:00Z"
        lastResyncAt={null}
      />,
    );
    expect(screen.getByRole("button", { name: /Sync/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Full Re-index/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Dead Code Scan/i })).toBeInTheDocument();
    expect(screen.getByText(/Last re-indexed/)).toBeInTheDocument();
    expect(screen.getByText(/never/i)).toBeInTheDocument();
  });

  it("opens confirm dialog for needsConfirm actions and forwards key on confirm", async () => {
    const onAction = vi.fn().mockResolvedValue(undefined);
    render(<QuickActions onAction={onAction} pageCount={50} modelName="gpt-5.4-mini" />);

    fireEvent.click(screen.getByRole("button", { name: /^Sync$/ }));
    expect(await screen.findByText("Sync Repository")).toBeInTheDocument();
    // The confirm button inside the dialog re-uses the same action label.
    const confirmButtons = screen.getAllByRole("button", { name: /^Sync$/ });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]!);
    await waitFor(() => expect(onAction).toHaveBeenCalledWith("sync"));
  });

  it("renders activeJobSlot in place of the buttons when provided", () => {
    render(
      <QuickActions
        onAction={vi.fn()}
        activeJobSlot={<div data-testid="job-progress">progress</div>}
      />,
    );
    expect(screen.getByTestId("job-progress")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Sync$/ })).not.toBeInTheDocument();
  });

  it("fires non-confirming actions immediately (dead-code path)", () => {
    const onAction = vi.fn().mockResolvedValue(undefined);
    render(<QuickActions onAction={onAction} />);
    fireEvent.click(screen.getByRole("button", { name: /Dead Code Scan/i }));
    expect(onAction).toHaveBeenCalledWith("dead-code");
  });
});
