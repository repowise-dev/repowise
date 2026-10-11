import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { THEME, KEYFRAMES } from "../../src/c4/theme/theme-variables";
import { MobileLayout, useIsMobile } from "../../src/c4/mobile/MobileLayout";
import { MobileBottomNav } from "../../src/c4/mobile/MobileBottomNav";
import { NodeTooltip } from "../../src/c4/panels/NodeTooltip";
import type { ArchNode } from "../../src/c4/types";

function createMatchMediaMock(matches: boolean) {
  const listeners: Array<(e: MediaQueryListEvent) => void> = [];
  const mql = {
    matches,
    media: "(max-width: 768px)",
    addEventListener: (_event: string, fn: (e: MediaQueryListEvent) => void) => {
      listeners.push(fn);
    },
    removeEventListener: (_event: string, fn: (e: MediaQueryListEvent) => void) => {
      const idx = listeners.indexOf(fn);
      if (idx >= 0) listeners.splice(idx, 1);
    },
    dispatchChange: (newMatches: boolean) => {
      for (const fn of listeners) {
        fn({ matches: newMatches } as MediaQueryListEvent);
      }
    },
  };
  return { mql, listeners };
}

function makeNode(overrides: Partial<ArchNode> = {}): ArchNode {
  return {
    id: "test-node",
    node_type: "file",
    name: "TestNode.ts",
    file_path: "src/TestNode.ts",
    line_range: null,
    summary: "A test node for tooltip testing",
    complexity: "moderate",
    tags: ["typescript", "core", "api", "utils"],
    language: "typescript",
    pagerank: 0.5,
    pagerank_percentile: 80,
    betweenness: 0.2,
    in_degree: 3,
    out_degree: 5,
    community_id: 1,
    is_entry_point: false,
    is_test: false,
    is_hotspot: false,
    is_dead: false,
    has_doc: false,
    primary_owner: null,
    primary_owner_pct: null,
    bus_factor: null,
    ...overrides,
  };
}

// =========================================================================
// Theme Variables
// =========================================================================

describe("Theme Variables", () => {
  it("test_theme_variables_defined - THEME has all required categories", () => {
    expect(THEME.canvas).toBeDefined();
    expect(THEME.canvas.bg).toContain("--color-bg-canvas");
    expect(THEME.surface).toBeDefined();
    expect(THEME.surface.elevated).toContain("--color-bg-elevated");
    expect(THEME.surface.glass).toBeDefined();
    expect(THEME.surface.glassBlur).toBe("blur(8px)");
    expect(THEME.border).toBeDefined();
    expect(THEME.border.default).toContain("--color-border-default");
    expect(THEME.text).toBeDefined();
    expect(THEME.text.primary).toContain("--color-text-primary");
    expect(THEME.text.secondary).toContain("--color-text-secondary");
    expect(THEME.accent).toBeDefined();
    expect(THEME.accent.primary).toContain("--color-accent-primary");
    expect(THEME.selection).toBeDefined();
    expect(THEME.complexity).toBeDefined();
    expect(THEME.complexity.simple).toContain("--color-success");
    expect(THEME.complexity.moderate).toContain("--color-warning");
    expect(THEME.complexity.complex).toContain("--color-error");
    expect(THEME.diff).toBeDefined();
    expect(THEME.font).toBeDefined();
    expect(THEME.radius).toBeDefined();
    expect(THEME.shadow).toBeDefined();

    expect(KEYFRAMES.accentPulse).toContain("@keyframes accentPulse");
    expect(KEYFRAMES.fadeSlideIn).toContain("@keyframes fadeSlideIn");
  });
});

// =========================================================================
// Mobile Detection
// =========================================================================

describe("Mobile Detection", () => {
  let originalMatchMedia: typeof window.matchMedia;

  beforeEach(() => {
    originalMatchMedia = window.matchMedia;
  });

  afterEach(() => {
    window.matchMedia = originalMatchMedia;
  });

  it("test_mobile_detection_small - useIsMobile returns true for small viewport", () => {
    const { mql } = createMatchMediaMock(true);
    window.matchMedia = vi.fn().mockReturnValue(mql);

    function TestComp() {
      const isMobile = useIsMobile();
      return <div data-testid="result">{String(isMobile)}</div>;
    }

    render(<TestComp />);
    expect(screen.getByTestId("result").textContent).toBe("true");
  });

  it("test_mobile_detection_large - useIsMobile returns false for large viewport", () => {
    const { mql } = createMatchMediaMock(false);
    window.matchMedia = vi.fn().mockReturnValue(mql);

    function TestComp() {
      const isMobile = useIsMobile();
      return <div data-testid="result">{String(isMobile)}</div>;
    }

    render(<TestComp />);
    expect(screen.getByTestId("result").textContent).toBe("false");
  });
});

// =========================================================================
// Mobile Tab Bar
// =========================================================================

describe("MobileBottomNav", () => {
  it("test_mobile_tab_bar_renders - three tabs with correct labels", () => {
    render(<MobileBottomNav activeTab="graph" onTabChange={() => {}} />);
    expect(screen.getByRole("tab", { name: "Graph tab" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Info tab" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Files tab" })).toBeInTheDocument();
  });
});

// =========================================================================
// Mobile Tab Switching
// =========================================================================

describe("MobileLayout", () => {
  let originalMatchMedia: typeof window.matchMedia;

  beforeEach(() => {
    originalMatchMedia = window.matchMedia;
    const { mql } = createMatchMediaMock(true);
    window.matchMedia = vi.fn().mockReturnValue(mql);
  });

  afterEach(() => {
    window.matchMedia = originalMatchMedia;
  });

  it("test_mobile_tab_switching - clicking tab changes active content", () => {
    render(
      <MobileLayout
        graphContent={<div data-testid="graph">Graph</div>}
        infoContent={<div data-testid="info">Info</div>}
        filesContent={<div data-testid="files">Files</div>}
      />,
    );

    const graphPanel = screen.getByTestId("mobile-tab-content-graph");
    expect(graphPanel.style.display).toBe("block");

    fireEvent.click(screen.getByRole("tab", { name: "Info tab" }));
    expect(screen.getByTestId("mobile-tab-content-info").style.display).toBe("block");
    expect(screen.getByTestId("mobile-tab-content-graph").style.display).toBe("none");
  });

  it("test_mobile_tab_persistence - inactive content stays in DOM (display:none, not unmounted)", () => {
    render(
      <MobileLayout
        graphContent={<div data-testid="graph">Graph</div>}
        infoContent={<div data-testid="info">Info</div>}
        filesContent={<div data-testid="files">Files</div>}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Info tab" }));

    expect(screen.getByTestId("graph")).toBeInTheDocument();
    expect(screen.getByTestId("mobile-tab-content-graph").style.display).toBe("none");

    expect(screen.getByTestId("files")).toBeInTheDocument();
    expect(screen.getByTestId("mobile-tab-content-files").style.display).toBe("none");
  });
});

// =========================================================================
// NodeTooltip
// =========================================================================

describe("NodeTooltip", () => {
  it("test_tooltip_renders - tooltip shows node data at position", () => {
    const node = makeNode();
    render(<NodeTooltip node={node} position={{ x: 100, y: 200 }} />);

    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    expect(screen.getByText("TestNode.ts")).toBeInTheDocument();
    expect(screen.getByText("file")).toBeInTheDocument();
    expect(screen.getByText("A test node for tooltip testing")).toBeInTheDocument();
  });

  it("test_tooltip_null_node - returns null when node is null", () => {
    const { container } = render(
      <NodeTooltip node={null} position={{ x: 100, y: 200 }} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("test_tooltip_truncation - long summary truncated at 120 chars", () => {
    const longSummary = "A".repeat(150);
    const node = makeNode({ summary: longSummary });
    render(<NodeTooltip node={node} position={{ x: 0, y: 0 }} />);

    const expectedText = "A".repeat(120) + "…";
    expect(screen.getByText(expectedText)).toBeInTheDocument();
  });
});

// =========================================================================
// Accessibility
// =========================================================================

describe("Accessibility", () => {
  it("test_aria_labels - all buttons in mobile nav have aria-labels", () => {
    render(<MobileBottomNav activeTab="graph" onTabChange={() => {}} />);

    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(3);
    for (const tab of tabs) {
      expect(tab).toHaveAttribute("aria-label");
      expect(tab.getAttribute("aria-label")).toBeTruthy();
    }
  });
});
