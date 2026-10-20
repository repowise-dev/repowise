import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  GraphNarrowingSelect,
  GraphScopeSwitcher,
} from "../../src/graph/graph-scope-controls.js";
import type { ModuleGroup } from "../../src/graph/use-module-filter.js";
import type { CommunitySummaryItem } from "@repowise-dev/types/graph";

const mockModules: ModuleGroup[] = [
  { id: "packages/ui", fileCount: 42 },
  { id: "packages/server", fileCount: 28 },
  { id: "packages/web", fileCount: 15 },
];

const mockCommunities: CommunitySummaryItem[] = [
  { community_id: 0, label: "Core Services", member_count: 50, cohesion: 0.85, top_file: "core.ts" },
  { community_id: 1, label: "Auth & Users", member_count: 35, cohesion: 0.72, top_file: "auth.ts" },
  { community_id: 2, label: "Graph Engine", member_count: 20, cohesion: 0.65, top_file: "graph.ts" },
];

describe("GraphNarrowingSelect", () => {
  it("renders 'All files' option and communities and modules", () => {
    const onChange = vi.fn();
    render(
      <GraphNarrowingSelect
        modules={mockModules}
        communities={mockCommunities}
        activeModule={null}
        activeCommunity={null}
        onChange={onChange}
      />,
    );

    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("");
    expect(screen.getByText("All files")).toBeTruthy();
    expect(screen.getByText("Core Services · 50")).toBeTruthy();
    expect(screen.getByText("packages/ui · 42")).toBeTruthy();
  });

  it("selects listed community when activeCommunity matches an item in communities", () => {
    const onChange = vi.fn();
    render(
      <GraphNarrowingSelect
        modules={mockModules}
        communities={mockCommunities}
        activeModule={null}
        activeCommunity={1}
        onChange={onChange}
      />,
    );

    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("c:1");
  });

  it("provides fallback option when activeCommunity is outside the provided communities (e.g. outside top 20)", () => {
    const onChange = vi.fn();
    render(
      <GraphNarrowingSelect
        modules={mockModules}
        communities={mockCommunities}
        activeModule={null}
        activeCommunity={27}
        onChange={onChange}
      />,
    );

    const select = screen.getByRole("combobox") as HTMLSelectElement;
    // The select value must match c:27 and NOT fall back to empty ("All files")
    expect(select.value).toBe("c:27");
    expect(screen.getByText("Community 27")).toBeTruthy();

    // Selecting "All files" should invoke onChange with nulls
    fireEvent.change(select, { target: { value: "" } });
    expect(onChange).toHaveBeenCalledWith({ module: null, community: null });
  });

  it("uses custom activeCommunityLabel when provided for unlisted community", () => {
    const onChange = vi.fn();
    render(
      <GraphNarrowingSelect
        modules={mockModules}
        communities={mockCommunities}
        activeModule={null}
        activeCommunity={27}
        activeCommunityLabel="Community 27 (Billing Subsystem)"
        onChange={onChange}
      />,
    );

    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("c:27");
    expect(screen.getByText("Community 27 (Billing Subsystem)")).toBeTruthy();
  });

  it("correctly handles activeCommunity = 0 even if not in communities list", () => {
    const onChange = vi.fn();
    render(
      <GraphNarrowingSelect
        modules={mockModules}
        communities={[]}
        activeModule={null}
        activeCommunity={0}
        onChange={onChange}
      />,
    );

    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("c:0");
    expect(screen.getByText("Community 0")).toBeTruthy();
  });

  it("renders staleModule option when activeModule is not in modules list", () => {
    const onChange = vi.fn();
    render(
      <GraphNarrowingSelect
        modules={mockModules}
        communities={mockCommunities}
        activeModule="packages/legacy"
        activeCommunity={null}
        onChange={onChange}
      />,
    );

    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("m:packages/legacy");
    expect(screen.getByText("packages/legacy · 0")).toBeTruthy();
  });

  it("calls onChange with module when a module is selected", () => {
    const onChange = vi.fn();
    render(
      <GraphNarrowingSelect
        modules={mockModules}
        communities={mockCommunities}
        activeModule={null}
        activeCommunity={null}
        onChange={onChange}
      />,
    );

    const select = screen.getByRole("combobox");
    fireEvent.change(select, { target: { value: "m:packages/ui" } });
    expect(onChange).toHaveBeenCalledWith({ module: "packages/ui", community: null });
  });

  it("calls onChange with community when a community is selected", () => {
    const onChange = vi.fn();
    render(
      <GraphNarrowingSelect
        modules={mockModules}
        communities={mockCommunities}
        activeModule={null}
        activeCommunity={null}
        onChange={onChange}
      />,
    );

    const select = screen.getByRole("combobox");
    fireEvent.change(select, { target: { value: "c:2" } });
    expect(onChange).toHaveBeenCalledWith({ module: null, community: 2 });
  });

  it("returns null when there are fewer than 2 modules and no communities and no active filters", () => {
    const { container } = render(
      <GraphNarrowingSelect
        modules={[{ id: "single", fileCount: 10 }]}
        communities={[]}
        activeModule={null}
        activeCommunity={null}
        onChange={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("still renders when activeCommunity is set even if modules < 2 and communities is empty", () => {
    const { container } = render(
      <GraphNarrowingSelect
        modules={[]}
        communities={[]}
        activeModule={null}
        activeCommunity={5}
        onChange={vi.fn()}
      />,
    );
    expect(container.firstChild).not.toBeNull();
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("c:5");
  });
});

describe("GraphScopeSwitcher", () => {
  it("renders scope buttons and handles clicks", () => {
    const onScopeChange = vi.fn();
    render(<GraphScopeSwitcher scope="files" onScopeChange={onScopeChange} />);

    const communitiesBtn = screen.getByRole("radio", { name: "Communities" });
    const filesBtn = screen.getByRole("radio", { name: "Files" });

    expect(filesBtn.getAttribute("aria-checked")).toBe("true");
    expect(communitiesBtn.getAttribute("aria-checked")).toBe("false");

    fireEvent.click(communitiesBtn);
    expect(onScopeChange).toHaveBeenCalledWith("communities");
  });

  it("handles keyboard navigation between radio options", () => {
    const onScopeChange = vi.fn();
    render(<GraphScopeSwitcher scope="communities" onScopeChange={onScopeChange} />);

    const group = screen.getByRole("radiogroup");
    fireEvent.keyDown(group, { key: "ArrowRight" });
    expect(onScopeChange).toHaveBeenCalledWith("files");

    fireEvent.keyDown(group, { key: "ArrowLeft" });
    expect(onScopeChange).toHaveBeenCalledWith("files");
  });
});
