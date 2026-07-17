import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  AttentionPanel,
  type AttentionItem,
  type AttentionItemType,
} from "../../src/dashboard/attention-panel";

function item(
  id: string,
  type: AttentionItemType,
  severity: AttentionItem["severity"] = "medium",
): AttentionItem {
  return { id, type, title: `title-${id}`, description: `desc-${id}`, severity };
}

/** Server order: strictly severity-sorted, which is what the panel re-mixes. */
function severitySorted(items: AttentionItem[]): AttentionItem[] {
  const rank = { high: 0, medium: 1, low: 2 } as const;
  return [...items].sort((a, b) => rank[a.severity] - rank[b.severity]);
}

describe("AttentionPanel", () => {
  it("collapses auto-proposed decisions into one row instead of listing them", () => {
    const items = [
      item("stale-1", "stale_decision", "high"),
      ...Array.from({ length: 40 }, (_, i) => item(`prop-${i}`, "proposed_decision")),
    ];

    render(<AttentionPanel items={items} repoId="r1" previewCount={5} />);

    expect(screen.getByText(/40/)).toBeInTheDocument();
    expect(screen.getByText(/auto-proposed decisions awaiting review/)).toBeInTheDocument();
    // None of the 40 proposals are rendered as their own rows.
    expect(screen.queryByText("title-prop-0")).not.toBeInTheDocument();
    expect(screen.getByText("title-stale-1")).toBeInTheDocument();
  });

  it("counts only the triage queue in the badge, not the proposal inbox", () => {
    const items = [
      item("silo-1", "knowledge_silo"),
      ...Array.from({ length: 99 }, (_, i) => item(`prop-${i}`, "proposed_decision")),
    ];

    render(<AttentionPanel items={items} repoId="r1" previewCount={5} />);

    // The badge is the triage count (1) — not 100, which would read as a
    // problem count when 99 of those are our own unreviewed suggestions.
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.queryByText("100")).not.toBeInTheDocument();
  });

  it("surfaces every type in the preview instead of letting the biggest win", () => {
    // Strict severity order would fill a 4-row preview with stale decisions
    // alone and never show the silo, hotspot, or dead-code categories.
    const items = severitySorted([
      ...Array.from({ length: 10 }, (_, i) => item(`stale-${i}`, "stale_decision", "high")),
      ...Array.from({ length: 10 }, (_, i) => item(`silo-${i}`, "knowledge_silo")),
      ...Array.from({ length: 10 }, (_, i) => item(`hot-${i}`, "ungoverned_hotspot")),
      ...Array.from({ length: 10 }, (_, i) => item(`dead-${i}`, "dead_code", "low")),
    ]);

    render(<AttentionPanel items={items} repoId="r1" previewCount={4} />);

    expect(screen.getByText("title-stale-0")).toBeInTheDocument();
    expect(screen.getByText("title-silo-0")).toBeInTheDocument();
    expect(screen.getByText("title-hot-0")).toBeInTheDocument();
    expect(screen.getByText("title-dead-0")).toBeInTheDocument();
    // Round-robin: no type takes a second slot before every type has one.
    expect(screen.queryByText("title-stale-1")).not.toBeInTheDocument();
  });

  it("keeps the most severe type leading the preview", () => {
    const items = severitySorted([
      item("dead-0", "dead_code", "low"),
      item("stale-0", "stale_decision", "high"),
      item("silo-0", "knowledge_silo"),
    ]);

    render(<AttentionPanel items={items} repoId="r1" previewCount={3} />);

    const rows = screen.getAllByText(/^title-/);
    expect(rows[0]).toHaveTextContent("title-stale-0");
  });

  it("still shows the proposal row when nothing else needs triage", () => {
    const items = Array.from({ length: 3 }, (_, i) => item(`prop-${i}`, "proposed_decision"));

    render(<AttentionPanel items={items} repoId="r1" previewCount={5} />);

    expect(screen.getByText(/auto-proposed decisions awaiting review/)).toBeInTheDocument();
    expect(screen.queryByText("Nothing needs attention")).not.toBeInTheDocument();
  });

  it("reports all clear only when both queues are empty", () => {
    render(<AttentionPanel items={[]} repoId="r1" />);

    expect(screen.getByText("Nothing needs attention")).toBeInTheDocument();
  });
});
