// @vitest-environment jsdom

import React from "react";
import { SWRConfig } from "swr";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ getMcpClientConfig: vi.fn() }));
vi.mock("@/lib/api/mcp-tools", () => mocks);

import { McpSection } from "./mcp-section";

const clients = [
  { id: "claude-code", display_name: "Claude Code" },
  { id: "codex", display_name: "Codex CLI" },
];

function answer(target: string) {
  return target === "codex"
    ? {
        repo_id: "r1",
        clients,
        target: "codex",
        display_name: "Codex CLI",
        config: '[mcp_servers.repowise]\ncommand = "repowise"',
        docs_url: null,
        profile: "core",
        tool_count: 5,
        always_load: null,
      }
    : {
        repo_id: "r1",
        clients,
        target: "claude-code",
        display_name: "Claude Code",
        config: '{"mcpServers": {}}',
        docs_url: "https://example.test/docs",
        profile: "standard",
        tool_count: 10,
        always_load: false,
      };
}

function renderSection(repos: { id: string; name: string }[]) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <McpSection repos={repos} />
    </SWRConfig>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("McpSection", () => {
  it("shows the first agent's printed config and what it lists, then the chosen agent's", async () => {
    mocks.getMcpClientConfig.mockImplementation(async (_repo: string, target?: string) =>
      answer(target ?? "claude-code"),
    );
    renderSection([{ id: "r1", name: "repo-one" }]);

    expect(await screen.findByText('{"mcpServers": {}}')).toBeTruthy();
    expect(mocks.getMcpClientConfig).toHaveBeenCalledWith("r1", undefined);
    expect(screen.getByText(/Claude Code lists 10 tools, the standard profile\./)).toBeTruthy();
    expect(screen.getByText(/mcp\.always_load is off/)).toBeTruthy();
    // One repository: no repository picker.
    expect(screen.queryByLabelText("Repository")).toBeNull();

    fireEvent.change(screen.getByLabelText("Agent"), { target: { value: "codex" } });
    await waitFor(() => expect(mocks.getMcpClientConfig).toHaveBeenLastCalledWith("r1", "codex"));
    expect(await screen.findByText(/Codex CLI lists 5 tools, the core profile\./)).toBeTruthy();
    expect(screen.queryByText(/mcp\.always_load/)).toBeNull();
  });

  it("asks for the chosen repository when there are several", async () => {
    mocks.getMcpClientConfig.mockImplementation(async () => answer("claude-code"));
    renderSection([
      { id: "r1", name: "repo-one" },
      { id: "r2", name: "repo-two" },
    ]);
    await screen.findByText('{"mcpServers": {}}');

    fireEvent.change(screen.getByLabelText("Repository"), { target: { value: "r2" } });
    await waitFor(() => expect(mocks.getMcpClientConfig).toHaveBeenLastCalledWith("r2", undefined));
  });

  it("says what to do with no repository, and shows a failed load", async () => {
    renderSection([]);
    expect(screen.getByText(/Add a repository first/)).toBeTruthy();
    expect(mocks.getMcpClientConfig).not.toHaveBeenCalled();
    cleanup();

    mocks.getMcpClientConfig.mockRejectedValue(new Error("Could not print the config"));
    renderSection([{ id: "r1", name: "repo-one" }]);
    expect((await screen.findByRole("alert")).textContent).toContain("Could not print the config");
  });
});
