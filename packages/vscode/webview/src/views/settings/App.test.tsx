import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { App } from "./App";
import type { WebviewHost } from "../../runtime/rpc";
import type {
  RepoInit,
  SettingsValues,
} from "../../../../src/shared/webviewMessages";

const REPO: RepoInit = { id: "r1", name: "repo", headCommit: null, defaultBranch: "main" };

const DEFAULTS: SettingsValues = {
  "diagnostics.enabled": true,
  "diagnostics.minSeverity": "high",
  "diagnostics.dimensions": ["defect", "maintainability", "performance"],
  "gutterHeat.enabled": true,
  "fileDecorations.enabled": true,
  "fileDecorations.maxScore": 4,
  "codeLens.enabled": true,
  "hover.enabled": true,
  "server.autoStart": "ask",
  "server.port": null,
  "cliPath": "",
  "risk.baseBranch": "",
};

/** Host whose updateSetting echoes the merged map back, like the real one. */
function makeHost(initial: Partial<SettingsValues> = {}) {
  const values: SettingsValues = { ...DEFAULTS, ...initial };
  const updateSetting = vi.fn((key: keyof SettingsValues, value: unknown) => {
    (values as Record<string, unknown>)[key] = value;
    return Promise.resolve({ ...values });
  });
  const host = {
    api: {
      getSettings: vi.fn(() => Promise.resolve({ ...values })),
      updateSetting,
    },
    onInit: () => () => {},
    onRefresh: () => () => {},
    onUpdateDone: () => () => {},
    onThemeChanged: () => () => {},
    ready: () => {},
    openFile: () => {},
    copyText: () => {},
    openExternal: () => {},
    openView: () => {},
    updateIndex: () => {},
    setTheme: () => {},
  } as unknown as WebviewHost;
  return { host, updateSetting };
}

function renderApp(host: WebviewHost) {
  return render(<App host={host} repo={REPO} params={{}} refreshToken={0} />);
}

describe("settings App", () => {
  afterEach(() => cleanup());

  it("renders grouped rows and reflects loaded values", async () => {
    const { host } = makeHost();
    renderApp(host);

    expect(await screen.findByText("Editor signals")).toBeTruthy();
    expect(screen.getByText("Server")).toBeTruthy();
    expect(screen.getByText("Branch risk")).toBeTruthy();

    const toggle = screen.getByLabelText("Problems panel findings");
    expect(toggle.getAttribute("aria-checked")).toBe("true");
  });

  it("writes and optimistically flips a toggle", async () => {
    const { host, updateSetting } = makeHost();
    renderApp(host);

    const toggle = await screen.findByLabelText("Problems panel findings");
    fireEvent.click(toggle);

    expect(updateSetting).toHaveBeenCalledWith("diagnostics.enabled", false);
    await waitFor(() =>
      expect(screen.getByLabelText("Problems panel findings").getAttribute("aria-checked")).toBe(
        "false",
      ),
    );
  });

  it("disables dependent controls when the parent signal is off", async () => {
    const { host } = makeHost({ "diagnostics.enabled": false });
    renderApp(host);

    const severity = (await screen.findByLabelText("Minimum severity")) as HTMLSelectElement;
    expect(severity.disabled).toBe(true);
  });
});
