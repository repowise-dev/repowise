import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DecisionCreateForm } from "../../src/decisions/decision-create-form";

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: (...args: unknown[]) => toastError(...args),
  },
}));

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe("DecisionCreateForm", () => {
  beforeEach(() => {
    toastSuccess.mockClear();
    toastError.mockClear();
  });

  it("submits every field, splitting the list inputs", async () => {
    const onSubmit = vi.fn(async () => undefined);
    const onCreated = vi.fn();
    render(<DecisionCreateForm onSubmit={onSubmit} onCreated={onCreated} />);

    fill("Title", "Use JWT for authentication");
    fill("Decision", "Issue signed JWTs");
    fill("Context", "sessions did not survive a restart");
    fill("Rationale", "stateless verification");
    fill("Rejected alternatives", "server sessions, opaque tokens");
    fill("Tradeoffs", "revocation needs a deny list");
    fill("Affected files", "src/auth/service.py, src/auth/middleware.py");
    fill("Tags", "auth, security");

    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        title: "Use JWT for authentication",
        decision: "Issue signed JWTs",
        context: "sessions did not survive a restart",
        rationale: "stateless verification",
        alternatives: ["server sessions", "opaque tokens"],
        consequences: ["revocation needs a deny list"],
        affected_files: ["src/auth/service.py", "src/auth/middleware.py"],
        tags: ["auth", "security"],
      }),
    );
    expect(onCreated).toHaveBeenCalled();
    expect(toastSuccess).toHaveBeenCalled();
  });

  it("blank list fields stay empty rather than becoming one empty entry", async () => {
    // "".split(",") is [""], so the naive read files a decision that names a
    // single unnamed file — and `affected_files` is what governance joins on.
    const onSubmit = vi.fn(async () => undefined);
    render(<DecisionCreateForm onSubmit={onSubmit} />);

    fill("Title", "Prefer ruff check");
    fill("Decision", "never ruff format");
    fireEvent.click(screen.getByRole("button", { name: "Save as candidate" }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({
          affected_files: [],
          consequences: [],
          alternatives: [],
          tags: [],
        }),
      ),
    );
  });

  it("cannot be submitted without a title and a decision", () => {
    render(<DecisionCreateForm onSubmit={vi.fn(async () => undefined)} />);

    const submit = screen.getByRole("button", { name: "Save as candidate" });
    expect(submit).toBeDisabled();

    fill("Title", "Half a record");
    expect(submit).toBeDisabled();

    fill("Decision", "the other half");
    expect(submit).toBeEnabled();
  });

  // The form and the acceptance contract have to agree before the write, not
  // after it. `record_acceptance` refuses an acceptance that names no scope,
  // so a form offering "Record decision" on an empty Affected files sent a
  // request that came back as "no scope: name the files or modules it
  // governs" in a failure toast, with the eight answered fields still on
  // screen and nothing saying which field was the problem.
  describe("says which outcome the write will have", () => {
    function fillRequired() {
      fill("Title", "Use JWT for authentication");
      fill("Decision", "Issue signed JWTs");
    }

    it("offers to record a decision once it names a scope", () => {
      render(<DecisionCreateForm onSubmit={vi.fn(async () => undefined)} />);
      fillRequired();
      fill("Affected files", "src/auth/service.py");

      expect(
        screen.getByRole("button", { name: "Record decision" }),
      ).toBeEnabled();
      expect(screen.getByText(/Recorded as confirmed/)).toBeInTheDocument();
    });

    it("offers to save a candidate while it names nothing", () => {
      render(<DecisionCreateForm onSubmit={vi.fn(async () => undefined)} />);
      fillRequired();

      expect(
        screen.getByRole("button", { name: "Save as candidate" }),
      ).toBeEnabled();
      expect(
        screen.getByText(/Name the files it governs to confirm it/),
      ).toBeInTheDocument();
    });

    it("switches the verb as the scope field is filled and cleared", () => {
      render(<DecisionCreateForm onSubmit={vi.fn(async () => undefined)} />);
      fillRequired();

      fill("Affected files", "src/auth/service.py");
      expect(screen.getByRole("button", { name: "Record decision" })).toBeTruthy();

      // A field holding only separators names nothing, the same way the
      // server reads it: splitList drops the blanks on both sides.
      fill("Affected files", " , ");
      expect(
        screen.getByRole("button", { name: "Save as candidate" }),
      ).toBeTruthy();
    });

    it("names the outcome in the success toast too", async () => {
      const onSubmit = vi.fn(async () => undefined);
      render(<DecisionCreateForm onSubmit={onSubmit} />);
      fillRequired();
      fireEvent.click(screen.getByRole("button", { name: "Save as candidate" }));

      await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
      expect(toastSuccess.mock.calls[0]?.[0]).toMatch(/candidate/i);
    });
  });

  it("keeps the form open and says so when the write fails", async () => {
    const onSubmit = vi.fn(async () => {
      throw new Error("repo is read-only");
    });
    const onCreated = vi.fn();
    render(<DecisionCreateForm onSubmit={onSubmit} onCreated={onCreated} />);

    fill("Title", "Use JWT");
    fill("Decision", "sign them");
    fireEvent.click(screen.getByRole("button", { name: "Save as candidate" }));

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(onCreated).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Title")).toHaveValue("Use JWT");
  });
});
