// @vitest-environment jsdom

import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ getMcpToolSurface: vi.fn(), updateMcpTools: vi.fn() }));
vi.mock("@/lib/api/mcp-tools", () => mocks);
const settings = vi.hoisted(() => ({ getSettings: vi.fn(), setSetting: vi.fn() }));
vi.mock("@/lib/api/settings", () => settings);

import { McpToolsSection } from "./mcp-tools-section";

const surface = {
  is_workspace: false,
  profile: null,
  profiles: { core: ["get_answer"], standard: ["get_answer"], all: ["get_answer", "get_dead_code"] },
  tools: [
    { name: "get_answer", enabled: true, default: true, requires_workspace: false, description: "Answers." },
    { name: "get_dead_code", enabled: false, default: false, requires_workspace: false, description: "Dead code." },
  ],
  command: "repowise config reset mcp.tools",
};

const alwaysLoad = {
  key: "mcp.always_load",
  type: "bool",
  description: "Claude Code only: load the read tools from the first turn.",
  area: "mcp",
  scope: "repo",
  value: false,
  default: false,
  source: "default",
  changed: false,
  writable: true,
};

function settingsWith(value: boolean) {
  return { areas: [{ area: "mcp", settings: [{ ...alwaysLoad, value }] }], suggestions: [] };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("McpToolsSection", () => {
  it("saves one repository's tools on change and rolls a failed write back", async () => {
    mocks.getMcpToolSurface.mockResolvedValue(surface);
    settings.getSettings.mockResolvedValue(settingsWith(false));
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
    settings.getSettings.mockResolvedValue(settingsWith(false));
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

  it("shows the registry's help and the CLI command for always-load, and saves it", async () => {
    mocks.getMcpToolSurface.mockResolvedValue(surface);
    settings.getSettings.mockResolvedValue(settingsWith(false));
    settings.setSetting.mockResolvedValue({ key: "mcp.always_load", after: { value: true, source: "repo_file", changed: true } });
    render(<McpToolsSection repoId="r1" />);

    const toggle = await screen.findByRole("switch", { name: "Load the read tools up front" });
    expect(screen.getByText(alwaysLoad.description)).toBeTruthy();
    expect(screen.getByText("repowise config set mcp.always_load false")).toBeTruthy();
    expect(screen.getByText("repowise config reset mcp.tools")).toBeTruthy();

    fireEvent.click(toggle);
    await waitFor(() => expect(settings.setSetting).toHaveBeenCalledWith("r1", "mcp.always_load", true));
    await waitFor(() => expect(screen.getByText("repowise config set mcp.always_load true")).toBeTruthy());
  });

  it("puts always-load back when the write fails", async () => {
    mocks.getMcpToolSurface.mockResolvedValue(surface);
    settings.getSettings.mockResolvedValue(settingsWith(false));
    settings.setSetting.mockRejectedValue(new Error("read-only file"));
    render(<McpToolsSection repoId="r1" />);

    fireEvent.click(await screen.findByRole("switch", { name: "Load the read tools up front" }));
    await waitFor(() => expect(screen.getByText("read-only file")).toBeTruthy());
    expect(
      screen.getByRole("switch", { name: "Load the read tools up front" }).getAttribute("aria-checked"),
    ).toBe("false");
  });

  it("drops a write for the previous repository when the repository changes", async () => {
    mocks.getMcpToolSurface.mockResolvedValue(surface);
    settings.getSettings.mockResolvedValue(settingsWith(false));
    let finish: (v: unknown) => void = () => {};
    settings.setSetting.mockImplementation(() => new Promise((resolve) => (finish = resolve)));
    const { rerender } = render(<McpToolsSection repoId="r1" />);
    const checked = () =>
      screen.getByRole("switch", { name: "Load the read tools up front" }).getAttribute("aria-checked");

    fireEvent.click(await screen.findByRole("switch", { name: "Load the read tools up front" }));
    await waitFor(() => expect(settings.setSetting).toHaveBeenCalledWith("r1", "mcp.always_load", true));
    rerender(<McpToolsSection repoId="r2" />);
    await waitFor(() => expect(settings.getSettings).toHaveBeenCalledWith("r2"));
    await waitFor(() => expect(checked()).toBe("false"));

    // r1's write lands late; r2's switch keeps r2's value.
    await act(async () => {
      finish({ key: "mcp.always_load", after: { value: true, source: "repo_file", changed: true } });
    });
    expect(checked()).toBe("false");
  });

  it("drops a tool write for the previous repository when the repository changes", async () => {
    mocks.getMcpToolSurface.mockResolvedValue(surface);
    settings.getSettings.mockResolvedValue(settingsWith(false));
    let finish: (v: unknown) => void = () => {};
    mocks.updateMcpTools.mockImplementation(() => new Promise((resolve) => (finish = resolve)));
    const { rerender } = render(<McpToolsSection repoId="r1" />);
    const checked = () => screen.getByRole("switch", { name: "get_dead_code" }).getAttribute("aria-checked");

    fireEvent.click(await screen.findByRole("switch", { name: "get_dead_code" }));
    await waitFor(() => expect(mocks.updateMcpTools).toHaveBeenCalled());
    rerender(<McpToolsSection repoId="r2" />);
    await waitFor(() => expect(mocks.getMcpToolSurface).toHaveBeenCalledWith("r2"));
    await waitFor(() => expect(checked()).toBe("false"));

    await act(async () => {
      finish({
        ...surface,
        tools: surface.tools.map((t) => ({ ...t, enabled: true })),
      });
    });
    expect(checked()).toBe("false");
  });

  it("leaves the switch out when the settings cannot be read", async () => {
    mocks.getMcpToolSurface.mockResolvedValue(surface);
    settings.getSettings.mockRejectedValue(new Error("offline"));
    render(<McpToolsSection repoId="r1" />);

    await screen.findByRole("switch", { name: "get_dead_code" });
    expect(screen.queryByRole("switch", { name: "Load the read tools up front" })).toBeNull();
  });
});
