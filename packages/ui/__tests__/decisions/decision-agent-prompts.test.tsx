import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  DecisionAgentHero,
  DecisionAgentPrompts,
} from "../../src/decisions/decision-agent-prompts";

describe("DecisionAgentPrompts", () => {
  it("copies the server's text for the prompt clicked", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(
      <DecisionAgentPrompts
        discover={{ text: "check these 12", label: "Check background records" }}
        agreements={{ text: "move these", label: "Move agreements" }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Check background records" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("check these 12"));
  });

  it("offers only the prompts that have text", () => {
    render(
      <DecisionAgentPrompts
        discover={null}
        agreements={{ text: "", label: "Move agreements" }}
        add={{ text: "record them", label: "Add decisions" }}
      />,
    );

    expect(screen.getByRole("button", { name: "Add decisions" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Move agreements" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check background records" })).not.toBeInTheDocument();
  });

  it("lists one record's prompts in the order given, skipping the missing ones", () => {
    render(
      <DecisionAgentPrompts
        prompts={[
          { text: "change it", label: "Change with your agent" },
          null,
          { text: "pin it", label: "Pin with your agent" },
        ]}
      />,
    );

    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Change with your agent",
      "Pin with your agent",
    ]);
  });

  it("renders nothing with no prompt", () => {
    const { container } = render(<DecisionAgentPrompts />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("DecisionAgentHero", () => {
  const discover = {
    kind: "discover",
    text: "Check the background decision records.\n\nRecords to check (12, busiest files first):\n- `f77b06bc` Make update reconcile",
    count: 12,
  };

  it("previews and copies exactly the prompt the server returned", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<DecisionAgentHero prompt={discover} />);

    expect(
      screen.getByRole("heading", { name: "Check 12 background records on the busiest files" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Prompt preview" })).toHaveTextContent(
      "Records to check (12, busiest files first):",
    );

    fireEvent.click(screen.getByRole("button", { name: "Copy prompt for your agent" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(discover.text));
    expect(screen.getByRole("status")).toHaveTextContent("Copied. Paste it into your agent.");
  });

  it("says when the clipboard refused the copy", async () => {
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn(async () => Promise.reject(new Error("denied"))) },
    });
    render(<DecisionAgentHero prompt={discover} />);

    fireEvent.click(screen.getByRole("button", { name: "Copy prompt for your agent" }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/Couldn't copy/));
  });

  it("leads with the host's eyebrow line, defaulting to the handoff", () => {
    const { rerender } = render(<DecisionAgentHero prompt={discover} />);
    expect(screen.getByText("Hand off to your agent")).toBeInTheDocument();
    rerender(<DecisionAgentHero prompt={discover} eyebrow="Claude Code made its first call 4 minutes ago" />);
    expect(screen.getByText("Claude Code made its first call 4 minutes ago")).toBeInTheDocument();
    expect(screen.queryByText("Hand off to your agent")).toBeNull();
  });

  it("names the add pass when the server had nothing to check", () => {
    render(<DecisionAgentHero prompt={{ kind: "add", text: "record them", count: 0 }} />);

    expect(
      screen.getByRole("heading", { name: "Record the decisions this repository already follows" }),
    ).toBeInTheDocument();
  });

  it("renders the host's links and handoff slot beside the button", () => {
    render(
      <DecisionAgentHero
        prompt={discover}
        links={<a href="#add">Record missed decisions</a>}
        handoff={<button type="button">Open in editor</button>}
      />,
    );

    expect(screen.getByRole("link", { name: "Record missed decisions" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open in editor" })).toBeInTheDocument();
  });

  it("offers the whole-queue prompt as a second copy button and copies its own text", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(
      <DecisionAgentHero
        prompt={discover}
        reviewAll={{ text: "Check every background record.", label: "Review all records" }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Review all records" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("Check every background record."));
    // The main prompt stays the primary action.
    expect(screen.getByRole("button", { name: "Copy prompt for your agent" })).toBeInTheDocument();
  });

  it("hides the whole-queue button when the host has no text for it", () => {
    const none = render(<DecisionAgentHero prompt={discover} reviewAll={null} />);
    expect(screen.queryByRole("button", { name: "Review all records" })).toBeNull();
    none.unmount();

    render(<DecisionAgentHero prompt={discover} reviewAll={{ text: "", label: "Review all records" }} />);
    expect(screen.queryByRole("button", { name: "Review all records" })).toBeNull();
  });

  it("gives a reader who cannot act one line instead of the prompt", () => {
    render(
      <DecisionAgentHero prompt={discover} readOnlyNote="Maintainers hand this to their agent." />,
    );

    expect(screen.getByText("Maintainers hand this to their agent.")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("region", { name: "Prompt preview" })).toBeNull();
  });

  it("holds the space while loading and renders nothing when the prompt is unavailable", () => {
    const loading = render(<DecisionAgentHero prompt={undefined} />);
    expect(loading.container.querySelector("[data-skeleton]")).not.toBeNull();
    loading.unmount();

    const failed = render(<DecisionAgentHero prompt={null} />);
    expect(failed.container).toBeEmptyDOMElement();
  });
});
