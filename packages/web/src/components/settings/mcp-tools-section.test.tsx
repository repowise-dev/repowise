// @vitest-environment jsdom

import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ getMcpToolSurface: vi.fn(), updateMcpTools: vi.fn() }));
vi.mock("@/lib/api/mcp-tools", () => mocks);

import { McpToolsSection } from "./mcp-tools-section";

const surface = {
  is_workspace: false,
  profile: null,
  profiles: { core: ["get_answer"], standard: ["get_answer"], all: ["get_answer", "get_dead_code"] },
  tools: [
    { name: "get_answer", enabled: true, default: true, requires_workspace: false, description: "Answers." },
    { name: "get_dead_code", enabled: false, default: false, requires_workspace: false, description: "Dead code." },
  ],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("McpToolsSection", () => {
  it("saves one repository's tools on change and rolls a failed write back", async () => {
    mocks.getMcpToolSurface.mockResolvedValue(surface);
    mocks.updateMcpTools.mockRejectedValue(new Error("disk full"));
    render(<McpToolsSection repoId="r1" />);

    const toggle = await screen.findByRole("switch", { name: "get_dead_code" });
    expect(mocks.getMcpToolSurface).toHaveBeenCalledWith("r1");
    fireEvent.click(toggle);

    await waitFor(() =>
      expect(mocks.updateMcpTools).toHaveBeenCalledWith({ repo_id: "r1", tools: ["+get_dead_code"] }),
    );
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "get_dead_code" }).getAttribute("aria-checked")).toBe("false"),
    );
    expect(screen.getByText("disk full")).toBeTruthy();
  });

  it("saves a chosen profile as the mcp.tools value and switches on top of it", async () => {
    mocks.getMcpToolSurface.mockResolvedValue(surface);
    mocks.updateMcpTools.mockImplementation(async () => ({ ...surface, profile: "core" }));
    render(<McpToolsSection repoId="r1" />);

    const select = await screen.findByRole("combobox");
    fireEvent.change(select, { target: { value: "core" } });
    await waitFor(() =>
      expect(mocks.updateMcpTools).toHaveBeenCalledWith({ repo_id: "r1", tools: ["core"] }),
    );

    fireEvent.click(screen.getByRole("switch", { name: "get_dead_code" }));
    await waitFor(() =>
      expect(mocks.updateMcpTools).toHaveBeenLastCalledWith({ repo_id: "r1", tools: ["core", "+get_dead_code"] }),
    );
  });
});
