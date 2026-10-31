import { describe, it, expect, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { MermaidDiagram } from "../../src/wiki/mermaid-diagram.js";

const mermaid = vi.hoisted(() => ({
  initialize: vi.fn(),
  render: vi.fn(async () => ({ svg: "<svg></svg>" })),
}));
vi.mock("mermaid", () => ({ default: mermaid }));
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    disconnect() {}
  },
);

async function themeCSS(): Promise<string> {
  render(<MermaidDiagram chart={"flowchart TB\n  a --> b"} />);
  await waitFor(() => expect(mermaid.initialize).toHaveBeenCalled());
  return mermaid.initialize.mock.calls.at(-1)![0].themeCSS as string;
}

describe("MermaidDiagram theme", () => {
  it("styles no node by position", async () => {
    // A linked node sits alone in its <a>, so a :first-of-type "entry node"
    // rule matched every linked node, and repainted one label in the fill's
    // own ink, which left a blank pill.
    const css = await themeCSS();
    expect(css).not.toMatch(/first-of-type|first-child|nth-/);
  });
});
