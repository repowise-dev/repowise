import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { SettingsRegistryPage } from "../../src/settings/settings-registry-page.js";
import { buildSettingAiPrompt } from "../../src/settings/setting-prompt.js";
import type {
  RegistryArea,
  RegistryChange,
  RegistrySetting,
} from "../../src/settings/settings-registry-types.js";

function setting(over: Partial<RegistrySetting> & Pick<RegistrySetting, "key" | "type">): RegistrySetting {
  return {
    description: `What ${over.key} does.`,
    area: "indexing",
    scope: "repo",
    source: "default",
    changed: false,
    writable: true,
    ...over,
  };
}

const AREAS: RegistryArea[] = [
  {
    area: "indexing",
    settings: [
      setting({ key: "follow_renames", type: "bool", value: true, default: true }),
      setting({
        key: "commit_limit",
        type: "int",
        value: 200,
        default: 5000,
        source: "repo_file",
        changed: true,
        minimum: 1,
        maximum: 10000,
      }),
      setting({ key: "exclude_patterns", type: "list", value: ["dist/"], default: [] }),
      setting({ key: "sql_dialect", type: "str", value: "auto", default: "auto", choices: ["auto", "postgres"] }),
    ],
  },
  {
    area: "account",
    settings: [
      setting({
        key: "REPOWISE_OFFLINE",
        area: "account",
        type: "bool",
        scope: "env",
        value: false,
        env: { name: "REPOWISE_OFFLINE" },
        writable: false,
      }),
      setting({
        key: "debug_knob",
        area: "account",
        type: "bool",
        value: false,
        internal: true,
      }),
    ],
  },
];

function change(key: string, value: unknown, extra: Partial<RegistryChange> = {}): RegistryChange {
  return {
    key,
    after: { value, source: "repo_file", changed: true },
    also: [],
    ...extra,
  };
}

function row(key: string): HTMLElement {
  const rows = screen.getAllByText(key).map((el) => el.closest(".group\\/row"));
  return rows.find(Boolean) as HTMLElement;
}

function renderPage(props: Partial<React.ComponentProps<typeof SettingsRegistryPage>> = {}) {
  const onSet = vi.fn(async (key: string, value: unknown) => change(key, value));
  const onReset = vi.fn(async (key: string) =>
    change(key, 5000, { after: { value: 5000, source: "default", changed: false } }),
  );
  render(
    <SettingsRegistryPage areas={AREAS} repoName="demo" onSet={onSet} onReset={onReset} {...props} />,
  );
  return { onSet, onReset };
}

describe("SettingsRegistryPage", () => {
  it("renders a section per area and the control each type calls for", () => {
    renderPage();
    expect(screen.getByText("Indexing")).toBeInTheDocument();
    expect(screen.getByText("Account")).toBeInTheDocument();
    expect(within(row("follow_renames")).getByRole("switch")).toBeChecked();
    expect(within(row("commit_limit")).getByRole("spinbutton")).toHaveValue(200);
    expect(within(row("exclude_patterns")).getByRole("textbox")).toHaveValue("dist/");
    expect(within(row("sql_dialect")).getByRole("combobox")).toHaveValue("auto");
  });

  it("marks a value set somewhere with its source and the default, and leaves defaults quiet", () => {
    renderPage();
    const limit = row("commit_limit");
    expect(within(limit).getByText("Repo file")).toBeInTheDocument();
    expect(within(limit).getByText(/Changed · default 5000/)).toBeInTheDocument();
    expect(within(row("follow_renames")).queryByText("Default")).toBeNull();
  });

  it("hides internal settings until asked", () => {
    renderPage();
    expect(screen.queryByText("debug_knob")).toBeNull();
    fireEvent.click(screen.getByLabelText("Show internal settings"));
    expect(screen.getByText("debug_knob")).toBeInTheDocument();
  });

  it("filters by search text and by changed", () => {
    renderPage();
    fireEvent.change(screen.getByLabelText("Search settings"), { target: { value: "exclude" } });
    expect(screen.getByText("exclude_patterns")).toBeInTheDocument();
    expect(screen.queryByText("commit_limit")).toBeNull();
    fireEvent.change(screen.getByLabelText("Search settings"), { target: { value: "" } });
    fireEvent.click(screen.getByLabelText("Changed only"));
    expect(screen.getByText("commit_limit")).toBeInTheDocument();
    expect(screen.queryByText("follow_renames")).toBeNull();
    expect(screen.getByText("1 of 5 settings")).toBeInTheDocument();
  });

  it("writes a switch, a number on blur, and a list as items", async () => {
    const { onSet } = renderPage();
    fireEvent.click(within(row("follow_renames")).getByRole("switch"));
    await waitFor(() => expect(onSet).toHaveBeenCalledWith("follow_renames", false));

    const limit = within(row("commit_limit")).getByRole("spinbutton");
    fireEvent.change(limit, { target: { value: "300" } });
    fireEvent.blur(limit);
    await waitFor(() => expect(onSet).toHaveBeenCalledWith("commit_limit", 300));

    const list = within(row("exclude_patterns")).getByRole("textbox");
    fireEvent.change(list, { target: { value: "dist/, build/" } });
    fireEvent.keyDown(list, { key: "Enter" });
    await waitFor(() => expect(onSet).toHaveBeenCalledWith("exclude_patterns", ["dist/", "build/"]));
  });

  it("rolls a field back and shows the server's message when a write fails", async () => {
    const onSet = vi.fn(async () => {
      throw new Error("commit_limit: must be at most 10000");
    });
    renderPage({ onSet });
    const limit = within(row("commit_limit")).getByRole("spinbutton");
    fireEvent.change(limit, { target: { value: "99999" } });
    fireEvent.blur(limit);
    expect(await screen.findByText("commit_limit: must be at most 10000")).toBeInTheDocument();
    expect(limit).toHaveValue(200);
  });

  it("resets a file value and applies the new state", async () => {
    const { onReset } = renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Reset commit_limit to its default" }));
    await waitFor(() => expect(onReset).toHaveBeenCalledWith("commit_limit"));
    await waitFor(() =>
      expect(within(row("commit_limit")).getByRole("spinbutton")).toHaveValue(5000),
    );
    expect(screen.queryByRole("button", { name: "Reset commit_limit to its default" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reset follow_renames to its default" })).toBeNull();
  });

  it("disables environment-only and locked settings and says why", () => {
    const locked: RegistryArea[] = [
      {
        area: "indexing",
        settings: [
          setting({
            key: "commit_limit",
            type: "int",
            value: 100,
            source: "org",
            changed: true,
            locked: true,
            locked_reason: "Your organization sets this.",
          }),
        ],
      },
      AREAS[1]!,
    ];
    renderPage({ areas: locked });
    expect(within(row("REPOWISE_OFFLINE")).getByRole("switch")).toBeDisabled();
    expect(screen.getByText("Read from REPOWISE_OFFLINE in the server's environment.")).toBeInTheDocument();
    const limit = row("commit_limit");
    expect(within(limit).getByRole("spinbutton")).toBeDisabled();
    expect(within(limit).getByText("Your organization sets this.")).toBeInTheDocument();
    expect(within(limit).getByText("org")).toBeInTheDocument();
    expect(within(limit).queryByRole("button", { name: /Reset/ })).toBeNull();
  });

  it("shows a problem the server reported", () => {
    const broken: RegistryArea[] = [
      {
        area: "indexing",
        settings: [
          setting({ key: "commit_limit", type: "int", value: 5000, problem: "commit_limit: must be at least 1" }),
        ],
      },
    ];
    renderPage({ areas: broken });
    expect(screen.getByRole("note")).toHaveTextContent("commit_limit: must be at least 1");
  });

  it("highlights suggestions at the top and applies one", async () => {
    const { onSet } = renderPage({
      suggestions: [
        {
          key: "commit_limit",
          suggested_value: 10000,
          reason: "This repository has a long history.",
          command: "repowise config set commit_limit 10000",
        },
      ],
    });
    expect(screen.getByText("Suggested for this repository")).toBeInTheDocument();
    expect(screen.getByText("This repository has a long history.")).toBeInTheDocument();
    expect(within(row("commit_limit")).getByText("Suggested: 10000")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Apply the suggested value for commit_limit" }));
    await waitFor(() => expect(onSet).toHaveBeenCalledWith("commit_limit", 10000));
  });

  it("links a setting to the page that owns its editor", () => {
    renderPage({
      linkFor: (s) => (s.key === "sql_dialect" ? { href: "/models", label: "Open Models" } : null),
    });
    expect(screen.getByRole("link", { name: "Open Models" })).toHaveAttribute("href", "/models");
  });

  it("uses translated labels", () => {
    renderPage({ labels: { search: "搜索设置", areaTitles: { indexing: "索引" } } });
    expect(screen.getByLabelText("搜索设置")).toBeInTheDocument();
    expect(screen.getByText("索引")).toBeInTheDocument();
  });
});

describe("buildSettingAiPrompt", () => {
  it("asks before changing and names the commands", () => {
    const prompt = buildSettingAiPrompt(AREAS[0]!.settings[1]!, "demo");
    expect(prompt).toContain("`commit_limit` in demo");
    expect(prompt).toContain("It is 200 now (from .repowise/config.yaml); the default is 5000.");
    expect(prompt).toContain("repowise config explain commit_limit");
    expect(prompt).toContain("once I agree");
    expect(prompt).toContain("repowise config get commit_limit");
  });

  it("carries a suggestion's value, reason and command", () => {
    const prompt = buildSettingAiPrompt(AREAS[0]!.settings[1]!, "demo", "generic", {
      key: "commit_limit",
      suggested_value: 10000,
      reason: "Long history.",
      command: "repowise config set commit_limit 10000",
    });
    expect(prompt).toContain("Repowise suggests 10000 for this repository: Long history.");
    expect(prompt).toContain("run `repowise config set commit_limit 10000`");
  });

  it("points an environment setting at the environment", () => {
    const prompt = buildSettingAiPrompt(AREAS[1]!.settings[0]!, "demo");
    expect(prompt).toContain("`REPOWISE_OFFLINE` environment variable");
    expect(prompt).not.toContain("config set");
  });
});
