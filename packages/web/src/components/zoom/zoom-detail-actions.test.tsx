// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatHandoffProvider } from "@repowise-dev/ui/chat";
import type { ZoomNode } from "@repowise-dev/ui/zoom";
import { ZoomDetailActions } from "./zoom-detail-actions";

function node(extra: Partial<ZoomNode>): ZoomNode {
  return {
    id: "zm:F:src/app.ts",
    parent_id: "zm:D:src",
    level: 4,
    kind: "file",
    name: "app.ts",
    path: "src/app.ts",
    children: [],
    importance: 0,
    sibling_rank: 0,
    metrics: {
      file_count: 1,
      descendant_count: 0,
      hotspot_count: 0,
      dead_count: 0,
      entry_point_count: 0,
      on_flow_count: 0,
    },
    summary: "",
    language: "ts",
    page_id: "",
    health_score: null,
    is_entry_point: false,
    is_hotspot: false,
    is_dead: false,
    is_test: false,
    on_flow: false,
    ...extra,
  };
}

describe("ZoomDetailActions", () => {
  afterEach(cleanup);

  it("offers only the destinations a layer has: no docs page, no file", () => {
    render(<ZoomDetailActions node={node({ kind: "layer", name: "Core", path: "" })} repoId="r1" />);
    expect(screen.queryByRole("link")).toBeNull();
    // No chat host mounted, so no Ask button that would lead nowhere.
    expect(screen.queryByRole("button", { name: "Ask about this" })).toBeNull();
    expect(screen.getByRole("button", { name: "Copy link" })).toBeTruthy();
  });

  it("links a file to its file page and a documented folder to its docs", () => {
    render(<ZoomDetailActions node={node({})} repoId="r1" />);
    expect(screen.getByRole("link", { name: "Open file" }).getAttribute("href")).toContain(
      "/repos/r1/files/src/app.ts",
    );
    cleanup();
    render(
      <ZoomDetailActions
        node={node({ kind: "folder", name: "Routers", path: "src/routers", page_id: "module_page:src/routers" })}
        repoId="r1"
      />,
    );
    expect(screen.getByRole("link", { name: "Read docs" }).getAttribute("href")).toContain("/repos/r1/docs");
    expect(screen.queryByRole("link", { name: "Open file" })).toBeNull();
  });

  it("asks chat about the node by its path", () => {
    const onHandoff = vi.fn();
    render(
      <ChatHandoffProvider onHandoff={onHandoff}>
        <ZoomDetailActions node={node({})} repoId="r1" />
      </ChatHandoffProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Ask about this" }));
    expect(onHandoff).toHaveBeenCalledWith(
      expect.objectContaining({
        context: { kind: "graph", label: "app.ts", target: "src/app.ts", targetKind: "path" },
      }),
    );
  });

  it("copies this page's URL focused on the node", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<ZoomDetailActions node={node({})} repoId="r1" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy link" }));
    expect(new URL(writeText.mock.calls[0]![0] as string).searchParams.get("focus")).toBe(
      "zm:F:src/app.ts",
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "Link copied" })).toBeTruthy());
  });
});
