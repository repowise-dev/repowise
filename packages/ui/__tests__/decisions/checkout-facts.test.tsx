import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { CheckoutFacts } from "../../src/decisions/checkout-facts.js";
import type { EpisodeSummary } from "@repowise-dev/types/episodes";

function makeFact(overrides: Partial<EpisodeSummary> = {}): EpisodeSummary {
  return {
    id: "e1",
    tier: "structural",
    kind: "formatter_drift",
    subject: "ruff format",
    evidence: "ruff format --check .: 419 files would be reformatted",
    nodes: [],
    node_count: 0,
    birth_commit: "acd24602cf51",
    birth_at: "2026-08-05T13:08:35Z",
    last_seen_at: "2026-08-05T13:08:35Z",
    still_true: null,
    ...overrides,
  };
}

describe("CheckoutFacts", () => {
  it("renders the evidence line, which is the fact", () => {
    render(<CheckoutFacts facts={[makeFact()]} available />);
    expect(
      screen.getByText(
        "ruff format --check .: 419 files would be reformatted",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("This tree is not formatter-clean"),
    ).toBeInTheDocument();
  });

  it("groups repeats of one kind under a single heading", () => {
    // Three console scripts are shadowed by one editable install. Three
    // identical headings would be the badge-on-every-row failure.
    render(
      <CheckoutFacts
        available
        facts={[
          makeFact({ id: "a", kind: "editable_shadow", evidence: "a.exe" }),
          makeFact({ id: "b", kind: "editable_shadow", evidence: "b.exe" }),
          makeFact({ id: "c", kind: "editable_shadow", evidence: "c.exe" }),
        ]}
      />,
    );
    expect(
      screen.getAllByText("An editable install shadows an installed command"),
    ).toHaveLength(1);
    expect(screen.getByText("a.exe")).toBeInTheDocument();
    expect(screen.getByText("c.exe")).toBeInTheDocument();
  });

  it("shows a kind it has no heading for rather than dropping it", () => {
    // `kind` is an unconstrained string on both sides of the wire and
    // producers add to it. Falling back is the difference between a new
    // detector appearing unlabelled and not appearing.
    render(
      <CheckoutFacts
        available
        facts={[makeFact({ kind: "vendored_lockfile", evidence: "two lockfiles" })]}
      />,
    );
    expect(screen.getByText("vendored_lockfile")).toBeInTheDocument();
    expect(screen.getByText("two lockfiles")).toBeInTheDocument();
  });

  it("says a trimmed scope is trimmed", () => {
    render(
      <CheckoutFacts
        available
        facts={[
          makeFact({
            kind: "nested_repos",
            nodes: ["backend", "frontend"],
            node_count: 8,
          }),
        ]}
      />,
    );
    expect(screen.getByText(/and 6 more/)).toBeInTheDocument();
  });

  it("renders nothing for an absent verdict, which means unchecked", () => {
    // `still_true: null` is unchecked, never stale. Printing "unverified" on
    // the one fact that cannot vouch for itself for free would read as doubt
    // about the fact rather than about the check.
    // Grepping for words the component cannot emit under any mutation is not
    // a test. Count the rendered paragraphs instead: dropping the
    // `fact.still_true &&` guard adds an empty one.
    const withVerdict = render(
      <CheckoutFacts
        facts={[makeFact({ id: "v", still_true: "re-observed" })]}
        available
      />,
    );
    const withCount = withVerdict.container.querySelectorAll("p").length;
    withVerdict.unmount();

    const { container } = render(
      <CheckoutFacts facts={[makeFact({ still_true: null })]} available />,
    );
    expect(container.querySelectorAll("p").length).toBe(withCount - 1);
    expect(container.textContent).not.toMatch(/unchecked|unverified|stale/i);
  });

  it("states a verdict the whole group shares once, not once per row", () => {
    const verdict = "re-observed by a later index (recorded 2026-08-05)";
    render(
      <CheckoutFacts
        available
        facts={[
          makeFact({ id: "a", kind: "editable_shadow", evidence: "a.exe", still_true: verdict }),
          makeFact({ id: "b", kind: "editable_shadow", evidence: "b.exe", still_true: verdict }),
          makeFact({ id: "c", kind: "editable_shadow", evidence: "c.exe", still_true: verdict }),
        ]}
      />,
    );
    expect(screen.getAllByText(verdict)).toHaveLength(1);
  });

  it("keeps verdicts per row when the group disagrees", () => {
    // Hoisting there would attribute one row's verdict to the other.
    render(
      <CheckoutFacts
        available
        facts={[
          makeFact({ id: "a", kind: "editable_shadow", evidence: "a.exe", still_true: "re-observed" }),
          makeFact({ id: "b", kind: "editable_shadow", evidence: "b.exe", still_true: null }),
        ]}
      />,
    );
    // Asserting the count alone stays green when `rows.every(...)` is forced
    // true, because a hoisted verdict also appears exactly once. The claim is
    // *where* it sits: inside a's row, not at group level where it would be
    // read as covering b too.
    const verdict = screen.getByText("re-observed");
    const owningRow = verdict.closest("li");
    expect(owningRow?.textContent).toContain("a.exe");
    expect(owningRow?.textContent).not.toContain("b.exe");
  });

  it("passes a free verdict through when the store has one", () => {
    render(
      <CheckoutFacts
        available
        facts={[
          makeFact({
            still_true: "re-observed by a later index (recorded 2026-08-05)",
          }),
        ]}
      />,
    );
    expect(
      screen.getByText("re-observed by a later index (recorded 2026-08-05)"),
    ).toBeInTheDocument();
  });

  it("tells an unreadable store apart from a clean tree", () => {
    const cold = render(<CheckoutFacts facts={[]} available={false} />);
    expect(cold.container.textContent).toMatch(/Facts land here once an index/);
    // Must not promise a future index will fill it: the router returns this
    // same flag when a read fails on a store that is already full.
    expect(cold.container.textContent).not.toMatch(/The next index/);
    cold.unmount();

    const clean = render(<CheckoutFacts facts={[]} available />);
    expect(clean.container.textContent).toMatch(/Nothing unusual/);
  });

  it("never says something is missing in either empty state", () => {
    // Copy rule: empty states say what will fill them.
    for (const available of [true, false]) {
      const { container, unmount } = render(
        <CheckoutFacts facts={[]} available={available} />,
      );
      expect(container.textContent).not.toMatch(/no data|not found|missing/i);
      unmount();
    }
  });
});
