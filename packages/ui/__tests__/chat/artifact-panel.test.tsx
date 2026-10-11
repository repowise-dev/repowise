import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ArtifactPanel } from "../../src/chat/artifact-panel.js";
import type { ChatArtifact } from "@repowise-dev/types/chat";

const riskArtifact: ChatArtifact = {
  id: "risk-1",
  version: 1,
  type: "risk",
  tool_name: "get_risk",
  title: "Modification risk",
  presentation: "risk",
  pinned: false,
  evidence: {
    basis: "measured",
    confidence: 0.91,
    coverage: { available: true },
    limits: { emitted: 25, total: 90 },
    truncated: true,
    stale: "Index is one commit behind",
  },
  data: { targets: { "src/hot.ts": { is_hotspot: true, hotspot_score: 0.97 } } },
};

const searchArtifact: ChatArtifact = {
  id: "search-1",
  version: 1,
  type: "search_results",
  tool_name: "search_codebase",
  title: "Search results",
  presentation: "search_results",
  evidence: { basis: "inferred" },
  data: { query: "chat", results: [] },
};

describe("ArtifactPanel workspace", () => {
  it("shows evidence beside the result and keeps raw JSON secondary", () => {
    render(
      <ArtifactPanel
        artifacts={[riskArtifact]}
        activeArtifactId="risk-1"
        open
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("Measured")).toBeInTheDocument();
    expect(screen.getByText(/Coverage available/)).toBeInTheDocument();
    expect(screen.getByText(/91% confidence/)).toBeInTheDocument();
    expect(screen.getByText(/25 of 90 shown/)).toBeInTheDocument();
    expect(screen.getByText("Truncated")).toBeInTheDocument();
    expect(screen.getByText(/one commit behind/)).toBeInTheDocument();
    expect(screen.getByText("Inspect raw result")).toBeInTheDocument();
    expect(screen.queryByText(/"src\/hot.ts"/)).not.toBeInTheDocument();
    const details = screen.getByText("Inspect raw result").closest("details")!;
    details.open = true;
    fireEvent(details, new Event("toggle", { bubbles: true }));
    expect(screen.getByText(/"src\/hot.ts"/)).toBeVisible();
  });

  it("exposes pin, compare, copy, export, source, and follow-up actions", () => {
    const onPin = vi.fn();
    const onCompare = vi.fn();
    const onOpenSource = vi.fn();
    const onFollowUp = vi.fn();
    Object.assign(navigator, { clipboard: { writeText: vi.fn() } });

    render(
      <ArtifactPanel
        artifacts={[riskArtifact, searchArtifact]}
        activeArtifactId="risk-1"
        open
        onClose={vi.fn()}
        onPin={onPin}
        onCompare={onCompare}
        onOpenSource={onOpenSource}
        onFollowUp={onFollowUp}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Pin artifact" }));
    fireEvent.click(screen.getByRole("button", { name: "Compare artifact" }));
    fireEvent.click(screen.getByRole("button", { name: "Copy artifact" }));
    fireEvent.click(screen.getByRole("button", { name: "Open artifact source" }));
    fireEvent.click(screen.getByRole("button", { name: "Follow up on artifact" }));

    expect(onPin).toHaveBeenCalledWith(riskArtifact, true);
    expect(onCompare).toHaveBeenCalledWith("risk-1");
    expect(navigator.clipboard.writeText).toHaveBeenCalled();
    expect(onOpenSource).toHaveBeenCalledWith(expect.objectContaining({ id: "risk-1", pinned: true }));
    expect(onFollowUp).toHaveBeenCalledWith(expect.stringContaining("Modification risk"));
    expect(screen.getByRole("button", { name: "Export artifact" })).toBeEnabled();
  });

  it("renders a selected comparison without changing the primary artifact", () => {
    render(
      <ArtifactPanel
        artifacts={[riskArtifact, searchArtifact]}
        activeArtifactId="risk-1"
        compareArtifactId="search-1"
        open
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByRole("region", { name: "Primary artifact" })).toHaveTextContent("src/hot.ts");
    expect(screen.getByRole("region", { name: "Comparison artifact" })).toHaveTextContent("No results found");
  });

  it("never compares an artifact with itself", () => {
    render(<ArtifactPanel artifacts={[riskArtifact, searchArtifact]} activeArtifactId="risk-1" compareArtifactId="risk-1" open onClose={vi.fn()} />);
    expect(screen.queryByRole("region", { name: "Comparison artifact" })).not.toBeInTheDocument();
  });

  it("keeps raw JSON unmounted after the panel is closed and reopened", () => {
    const view = render(<ArtifactPanel artifacts={[riskArtifact]} open onClose={vi.fn()} />);
    const details = screen.getByText("Inspect raw result").closest("details")!;
    details.open = true;
    fireEvent(details, new Event("toggle", { bubbles: true }));
    expect(screen.getByText(/"src\/hot.ts"/)).toBeInTheDocument();
    view.rerender(<ArtifactPanel artifacts={[riskArtifact]} open={false} onClose={vi.fn()} />);
    view.rerender(<ArtifactPanel artifacts={[riskArtifact]} open onClose={vi.fn()} />);
    expect(screen.queryByText(/"src\/hot.ts"/)).not.toBeInTheDocument();
  });

  it("keeps an optimistic pin while switching artifacts", () => {
    const view = render(<ArtifactPanel artifacts={[riskArtifact, searchArtifact]} activeArtifactId="risk-1" open onClose={vi.fn()} onPin={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Pin artifact" }));
    view.rerender(<ArtifactPanel artifacts={[riskArtifact, searchArtifact]} activeArtifactId="search-1" open onClose={vi.fn()} onPin={vi.fn()} />);
    view.rerender(<ArtifactPanel artifacts={[riskArtifact, searchArtifact]} activeArtifactId="risk-1" open onClose={vi.fn()} onPin={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Unpin artifact" })).toBeInTheDocument();
  });

  it("shows tool errors without invoking a typed renderer", () => {
    render(<ArtifactPanel artifacts={[{ ...riskArtifact, type: "overview", data: { error: "Index unavailable" } }]} open onClose={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Index unavailable");
  });

  it("contains incomplete typed results in a malformed state", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(<ArtifactPanel artifacts={[{ ...riskArtifact, type: "overview", data: {} }]} open onClose={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/incomplete or malformed/i);
    error.mockRestore();
  });
});
