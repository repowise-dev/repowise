import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import {
  AddRepoWizard,
  type AddRepoWizardAdapter,
  type AddRepoPreflightResult,
} from "../../src/onboarding/add-repo-wizard.js";

function makePreflight(overrides?: Partial<AddRepoPreflightResult>): AddRepoPreflightResult {
  return {
    provider: { ok: true, name: "gemini", model: "gemini-flash", error: null },
    file_count: 120,
    estimate: {
      total_pages: 40,
      estimated_cost_usd: 0.8,
      cost_low_usd: 0.5,
      cost_high_usd: 1.2,
      is_calibrated: false,
    },
    ...overrides,
  };
}

function makeAdapter(overrides?: Partial<AddRepoWizardAdapter>): AddRepoWizardAdapter {
  return {
    createRepo: vi.fn().mockResolvedValue({ id: "r1", name: "demo" }),
    preflight: vi.fn().mockResolvedValue(makePreflight()),
    startIndex: vi.fn().mockResolvedValue({ job_id: "job-1" }),
    onDone: vi.fn(),
    settingsHref: "/settings",
    ...overrides,
  };
}

async function fillAndSubmitDetails() {
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "demo" } });
  fireEvent.change(screen.getByLabelText("Local Path"), {
    target: { value: "C:\\repos\\demo" },
  });
  fireEvent.click(screen.getByRole("button", { name: /Continue/ }));
}

describe("AddRepoWizard", () => {
  it("registers without indexing, then auto-starts when the estimate clears the cost gate", async () => {
    const adapter = makeAdapter();
    render(<AddRepoWizard adapter={adapter} open onOpenChange={vi.fn()} />);

    await fillAndSubmitDetails();

    await waitFor(() => expect(adapter.onDone).toHaveBeenCalledWith("r1", "job-1"));
    expect(adapter.createRepo).toHaveBeenCalledWith(
      expect.objectContaining({ name: "demo", local_path: "C:\\repos\\demo" }),
    );
    expect(adapter.preflight).toHaveBeenCalledWith("r1");
    expect(adapter.startIndex).toHaveBeenCalledWith("r1");
  });

  it("stops for explicit confirmation above the cost gate", async () => {
    const adapter = makeAdapter({
      preflight: vi.fn().mockResolvedValue(
        makePreflight({
          estimate: {
            total_pages: 900,
            estimated_cost_usd: 7.4,
            cost_low_usd: 5.0,
            cost_high_usd: 11.0,
            is_calibrated: true,
          },
        }),
      ),
    });
    render(<AddRepoWizard adapter={adapter} open onOpenChange={vi.fn()} />);

    await fillAndSubmitDetails();

    expect(await screen.findByText(/Estimated generation cost/)).toBeTruthy();
    expect(screen.getByText(/\$5\.00 - \$11\.00/)).toBeTruthy();
    expect(adapter.startIndex).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Start indexing/ }));
    await waitFor(() => expect(adapter.startIndex).toHaveBeenCalledWith("r1"));
    await waitFor(() => expect(adapter.onDone).toHaveBeenCalledWith("r1", "job-1"));
  });

  it("surfaces a broken provider with recovery paths and never starts a job", async () => {
    const adapter = makeAdapter({
      preflight: vi.fn().mockResolvedValue(
        makePreflight({
          provider: { ok: false, name: "gemini", model: null, error: "invalid API key" },
          estimate: null,
        }),
      ),
    });
    render(<AddRepoWizard adapter={adapter} open onOpenChange={vi.fn()} />);

    await fillAndSubmitDetails();

    expect(await screen.findByText(/gemini check failed/)).toBeTruthy();
    expect(screen.getByText(/invalid API key/)).toBeTruthy();
    expect(screen.getByRole("link", { name: /Provider settings/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Retry check/ })).toBeTruthy();
    expect(adapter.startIndex).not.toHaveBeenCalled();

    // "Finish without indexing" still lands the user on the registered repo.
    fireEvent.click(screen.getByRole("button", { name: /Finish without indexing/ }));
    expect(adapter.onDone).toHaveBeenCalledWith("r1", null);
  });

  it("anchors path-shaped registration failures to the path field", async () => {
    const adapter = makeAdapter({
      createRepo: vi
        .fn()
        .mockRejectedValue(new Error("local_path is not a git repository")),
    });
    render(<AddRepoWizard adapter={adapter} open onOpenChange={vi.fn()} />);

    await fillAndSubmitDetails();

    expect(await screen.findByText(/not a git repository/)).toBeTruthy();
    expect(adapter.preflight).not.toHaveBeenCalled();
    // Still on the details step, ready to correct the path.
    expect(screen.getByLabelText("Local Path")).toBeTruthy();
  });
});
