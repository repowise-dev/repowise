import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import type { PluginsResponse } from "@repowise-dev/types/generated/http";
import { ALL_SOURCES, PluginsPanel } from "../../src/settings/plugins-panel.js";

const data: PluginsResponse = {
  api_version: 1,
  plugins: [
    {
      name: "pgstat",
      source: "entry point",
      target: "repowise_pgstat:register",
      status: "loaded",
      error: null,
      seams: ["evidence-source:pgstat", "setting:plugins.pgstat.db", "setting:REPOWISE_PLUGIN_PGSTAT_TOKEN"],
      version: "0.2.0",
      configured: false,
      missing: [{ key: "REPOWISE_PLUGIN_PGSTAT_TOKEN", fix: "export REPOWISE_PLUGIN_PGSTAT_TOKEN=<value>" }],
      settings: [
        { key: "plugins.pgstat.db", env: "REPOWISE_PLUGIN_PGSTAT_DB", secret: false, set: true },
        { key: "REPOWISE_PLUGIN_PGSTAT_TOKEN", env: "REPOWISE_PLUGIN_PGSTAT_TOKEN", secret: true, set: false },
      ],
      next_command: "export REPOWISE_PLUGIN_PGSTAT_TOKEN=<value>",
    },
    {
      name: "broken",
      source: "env",
      target: "broken_mod",
      status: "failed",
      error: "ImportError: no module named broken_mod",
      seams: [],
      next_command: "repowise plugins check",
    },
  ],
  sources: [
    {
      source: "forge",
      seam: "issue-source",
      plugin: null,
      configured: true,
      next_command: "repowise sources sync --source forge",
      last_sync: {
        at: "2026-11-28T10:00:00+00:00",
        state: "stored",
        stored_at: "2026-11-28T10:00:00+00:00",
        batches: 3,
        message: null,
      },
    },
    {
      source: "pgstat",
      seam: "evidence-source",
      plugin: "pgstat",
      configured: false,
      message: "needs REPOWISE_PLUGIN_PGSTAT_TOKEN",
      next_command: "export REPOWISE_PLUGIN_PGSTAT_TOKEN=<value>",
      last_sync: null,
    },
  ],
  syncing: [],
  sync_error: null,
};

describe("PluginsPanel", () => {
  it("shows each plugin's state, settings without values, and the command to finish setup", () => {
    render(
      <PluginsPanel
        data={data}
        guideHref="/guide"
        settingHref={(key) => `/settings/all?q=${key}`}
      />,
    );
    expect(screen.getByText("0.2.0")).toBeInTheDocument();
    expect(screen.getAllByText("Not configured").length).toBeGreaterThan(0);
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("ImportError: no module named broken_mod")).toBeInTheDocument();
    // A plain setting links to the settings editor; a secret never does.
    expect(screen.getByRole("link", { name: "plugins.pgstat.db" })).toHaveAttribute(
      "href",
      "/settings/all?q=plugins.pgstat.db",
    );
    expect(screen.queryByRole("link", { name: "REPOWISE_PLUGIN_PGSTAT_TOKEN" })).toBeNull();
    expect(screen.getByText("secret, not set")).toBeInTheDocument();
    expect(screen.getAllByText("export REPOWISE_PLUGIN_PGSTAT_TOKEN=<value>").length).toBeGreaterThan(0);
    expect(screen.getByText("repowise plugins check")).toBeInTheDocument();
    expect(screen.queryByText("No plugins installed")).toBeNull();
  });

  it("lists built-in sources with their last sync and syncs one on click", async () => {
    const onSync = vi.fn().mockResolvedValue(undefined);
    render(<PluginsPanel data={data} guideHref="/guide" onSync={onSync} />);
    expect(screen.getByText("Built in")).toBeInTheDocument();
    expect(screen.getByText("Stored 3 batches")).toBeInTheDocument();
    // Only a configured source can sync.
    const buttons = screen.getAllByRole("button", { name: /Sync .* now/ });
    expect(buttons).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Sync forge now" }));
    await waitFor(() => expect(onSync).toHaveBeenCalledWith("forge"));
  });

  it("shows a running sync and disables Sync now until it ends", () => {
    render(
      <PluginsPanel data={{ ...data, syncing: [ALL_SOURCES] }} guideHref="/guide" onSync={vi.fn()} />,
    );
    expect(screen.getByText("Syncing...")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sync forge now" })).toBeDisabled();
  });

  it("reports a sync that did not run", () => {
    render(
      <PluginsPanel
        data={{ ...data, sync_error: "This repository has no index to store into." }}
        guideHref="/guide"
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("no index");
  });

  it("with no plugin installed, keeps built-in sources and explains how to add one", () => {
    render(
      <PluginsPanel
        data={{ ...data, plugins: [], sources: data.sources!.slice(0, 1) }}
        guideHref="/guide"
        emptyExtra={<p>hosted tip</p>}
      />,
    );
    expect(screen.getByText("forge")).toBeInTheDocument();
    const empty = screen.getByRole("heading", { name: "No plugins installed" }).parentElement!;
    expect(within(empty).getByRole("link", { name: "Read the plugin guide" })).toHaveAttribute(
      "href",
      "/guide",
    );
    expect(within(empty).getByText("repowise plugins new <seam>")).toBeInTheDocument();
    expect(within(empty).getByText("hosted tip")).toBeInTheDocument();
  });
});
