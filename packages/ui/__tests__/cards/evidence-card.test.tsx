import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { EvidenceCard } from "@repowise-dev/types/cards";
import type { ChatUIMessage } from "@repowise-dev/types/chat";
import { EvidenceCardView, cardRefHref } from "../../src/cards/evidence-card.js";
import { ChatMessage } from "../../src/chat/chat-message.js";

const CHANGE: EvidenceCard = {
  schema_version: 1,
  kind: "change",
  title: "Session store rewrite",
  summary: "Moves sessions to the new store.",
  confidence: "medium",
  change: { type: "commit", id: "4c1e31f0a2b3" },
  facts: [
    { label: "Files touched", value: 1200, basis: { kind: "measured", source: "git history" } },
    {
      label: "Component",
      value: "auth",
      basis: { kind: "declared", source: ".repowise/components.yaml" },
      ref: { type: "component", id: "auth" },
    },
  ],
  evidence: [
    {
      statement: "Store is imported by 14 modules",
      basis: { kind: "inferred", source: "import graph" },
      refs: [{ type: "file", id: "src/auth/session.py", start_line: 10, end_line: 42 }],
    },
  ],
  links: [
    { label: "Store class", ref: { type: "symbol", id: "src/auth/session.py::Store" } },
    { label: "Payments", ref: { type: "component", id: "payments" } },
  ],
  generated_at: "2026-10-14T09:30:00Z",
};

const href = (ref: Parameters<typeof cardRefHref>[0]) => cardRefHref(ref, "/repos/r1");

describe("EvidenceCardView", () => {
  it("is a labelled region carrying its kind, confidence and subject", () => {
    render(<EvidenceCardView card={CHANGE} buildRefHref={href} />);
    const card = screen.getByRole("region", { name: "Session store rewrite" });
    expect(card).toHaveAttribute("data-card-kind", "change");
    expect(within(card).getByText(/Change · medium confidence/)).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "4c1e31f" })).toHaveAttribute(
      "href",
      "/repos/r1/commits?commit=4c1e31f0a2b3",
    );
  });

  it("spells out every basis in text, with its source", () => {
    const { container } = render(<EvidenceCardView card={CHANGE} />);
    expect(screen.getByText(/Measured · git history/)).toBeInTheDocument();
    expect(screen.getByText(/Declared · \.repowise\/components\.yaml/)).toBeInTheDocument();
    expect(screen.getByText(/Inferred · import graph/)).toBeInTheDocument();
    expect(container.querySelectorAll("[data-card-basis]")).toHaveLength(3);
  });

  it("formats numbers, and links refs the host can route", () => {
    render(<EvidenceCardView card={CHANGE} buildRefHref={href} />);
    expect(screen.getByText((1200).toLocaleString())).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "session.py:10-42" })).toHaveAttribute(
      "href",
      "/repos/r1/files/src/auth/session.py",
    );
    expect(screen.getByRole("link", { name: "Store class" })).toHaveAttribute(
      "href",
      `/repos/r1/symbols/${encodeURIComponent("src/auth/session.py::Store")}`,
    );
    // No dashboard route for a component: text, not a dead link.
    expect(screen.queryByRole("link", { name: "Payments" })).toBeNull();
    expect(screen.getByText("Payments")).toBeInTheDocument();
  });

  it("renders refs as text when the host gives no resolver", () => {
    render(<EvidenceCardView card={CHANGE} />);
    expect(screen.queryAllByRole("link")).toHaveLength(0);
  });

  it("numbers suspect rows, which arrive in rank order", () => {
    const suspects: EvidenceCard = {
      ...CHANGE,
      kind: "suspects",
      incident: "Checkout errors",
    } as EvidenceCard;
    render(<EvidenceCardView card={suspects} />);
    expect(screen.getByRole("list", { name: "Evidence" }).className).toContain("list-decimal");
    expect(screen.getByText("Checkout errors")).toBeInTheDocument();
  });

  it("keeps list semantics on the styled lists", () => {
    render(<EvidenceCardView card={CHANGE} />);
    expect(screen.getByRole("list", { name: "Evidence" })).toHaveAttribute("role", "list");
    expect(screen.getByRole("list", { name: "Links" })).toHaveAttribute("role", "list");
  });

  it("uses theme tokens only", () => {
    const { container } = render(<EvidenceCardView card={CHANGE} buildRefHref={href} />);
    expect(container.innerHTML).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgb\(/);
  });
});

describe("ChatMessage cards", () => {
  const message: ChatUIMessage = {
    id: "a1",
    role: "assistant",
    text: "Here is the change.",
    toolCalls: [],
    isStreaming: false,
    cards: [CHANGE],
  };

  it("renders a finished turn's cards with the chat link prefix", () => {
    render(<ChatMessage message={message} repoId="r1" linkPrefix="/w/acme" />);
    expect(screen.getByRole("region", { name: "Session store rewrite" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "session.py:10-42" })).toHaveAttribute(
      "href",
      "/w/acme/files/src/auth/session.py",
    );
  });

  it("holds cards back while the turn streams", () => {
    render(<ChatMessage message={{ ...message, isStreaming: true }} repoId="r1" />);
    expect(screen.queryByRole("region", { name: "Session store rewrite" })).toBeNull();
  });
});
