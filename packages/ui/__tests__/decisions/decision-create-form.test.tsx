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
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));

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

    const submit = screen.getByRole("button", { name: "Record decision" });
    expect(submit).toBeDisabled();

    fill("Title", "Half a record");
    expect(submit).toBeDisabled();

    fill("Decision", "the other half");
    expect(submit).toBeEnabled();
  });

  it("keeps the form open and says so when the write fails", async () => {
    const onSubmit = vi.fn(async () => {
      throw new Error("repo is read-only");
    });
    const onCreated = vi.fn();
    render(<DecisionCreateForm onSubmit={onSubmit} onCreated={onCreated} />);

    fill("Title", "Use JWT");
    fill("Decision", "sign them");
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(onCreated).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Title")).toHaveValue("Use JWT");
  });
});
