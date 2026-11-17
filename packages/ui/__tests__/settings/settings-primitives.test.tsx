import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  CopyLine,
  EnvVarLine,
  SaveIndicator,
  SettingsAdvanced,
  SettingsRow,
  StatusLine,
  useSaveState,
} from "../../src/settings/settings-primitives.js";
import { act, renderHook } from "@testing-library/react";

describe("SaveIndicator", () => {
  it("renders nothing at rest", () => {
    // Rule 10: a permanent "Saved" is a badge every row carries.
    const { container } = render(<SaveIndicator state="idle" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("announces each transient state politely", () => {
    const { rerender } = render(<SaveIndicator state="saving" />);
    expect(screen.getByText(/Saving/)).toBeInTheDocument();

    rerender(<SaveIndicator state="saved" />);
    const saved = screen.getByText("Saved");
    expect(saved).toBeInTheDocument();
    expect(saved.closest("[aria-live]")).toHaveAttribute("aria-live", "polite");

    rerender(<SaveIndicator state="error" error="Disk full" />);
    expect(screen.getByText("Disk full")).toBeInTheDocument();
  });

  it("falls back to a message when an error carries none", () => {
    render(<SaveIndicator state="error" />);
    expect(screen.getByText("Could not save")).toBeInTheDocument();
  });
});

describe("StatusLine", () => {
  it("uses the success/error pair, never the freshness pair", () => {
    // Three settings components signalled "the server answered" with
    // --color-fresh / --color-outdated, which carry doc-staleness semantics
    // everywhere else in the app.
    const { container: ok } = render(
      <StatusLine status="ok">Connected</StatusLine>,
    );
    expect(ok.innerHTML).toContain("--color-success");
    expect(ok.innerHTML).not.toContain("--color-fresh");

    const { container: bad } = render(
      <StatusLine status="error">Refused</StatusLine>,
    );
    expect(bad.innerHTML).toContain("--color-error");
    expect(bad.innerHTML).not.toContain("--color-outdated");
  });
});

describe("CopyLine", () => {
  it("never truncates the value it exists to hand you", () => {
    // Rule 6. The webhook URL is the one string on that page whose whole job is
    // to be read and copied; it used to render with `truncate`.
    const url = "https://a-fairly-long-host.example.com/api/webhooks/github";
    const { container } = render(<CopyLine value={url} label="GitHub" />);
    const code = container.querySelector("code")!;
    expect(code.textContent).toBe(url);
    expect(code.className).not.toContain("truncate");
    expect(code.className).toContain("overflow-x-auto");
  });

  it("gives the label the mono micro-label treatment", () => {
    render(<CopyLine value="x" label="GitHub" />);
    const label = screen.getByText("GitHub");
    expect(label.className).toContain("font-mono");
    expect(label.className).toContain("uppercase");
  });
});

describe("EnvVarLine", () => {
  it("renders nothing when there is nothing to require", () => {
    const { container } = render(<EnvVarLine vars={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists vars as mono code without a nested box", () => {
    const { container } = render(
      <EnvVarLine vars={["GEMINI_API_KEY", "OTHER"]} />,
    );
    const codes = container.querySelectorAll("code");
    expect([...codes].map((c) => c.textContent)).toEqual([
      "GEMINI_API_KEY",
      "OTHER",
    ]);
    expect(container.querySelector("[class*='border-dashed']")).toBeNull();
  });
});

describe("SettingsRow", () => {
  it("associates its label with the control it names", () => {
    render(
      <SettingsRow label="Server URL" htmlFor="api-url" hint="Where to reach it.">
        <input id="api-url" />
      </SettingsRow>,
    );
    expect(screen.getByLabelText("Server URL")).toHaveAttribute("id", "api-url");
    expect(screen.getByText("Where to reach it.")).toBeInTheDocument();
  });
});

describe("SettingsRow save tick", () => {
  it("shows its own save state beside the label, nothing at rest", () => {
    const { rerender } = render(
      <SettingsRow label="Name">
        <input />
      </SettingsRow>,
    );
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
    rerender(
      <SettingsRow label="Name" saveState="saved">
        <input />
      </SettingsRow>,
    );
    expect(screen.getByText("Saved")).toBeInTheDocument();
  });
});

describe("SettingsAdvanced", () => {
  it("is folded by default and names what it holds", () => {
    render(
      <SettingsAdvanced hint="Rare knobs.">
        <p>inside</p>
      </SettingsAdvanced>,
    );
    const details = screen.getByText("Advanced").closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(screen.getByText("Rare knobs.")).toBeInTheDocument();
  });
});

describe("useSaveState", () => {
  it("reports saved, then failed, and ignores a stale write", async () => {
    const { result } = renderHook(() => useSaveState());
    let outcome: string = "";
    await act(async () => {
      outcome = await result.current.track(() => Promise.resolve());
    });
    expect(outcome).toBe("saved");
    expect(result.current.state).toBe("saved");

    await act(async () => {
      outcome = await result.current.track(
        () => Promise.reject(new Error("x")),
        () => "Disk full",
      );
    });
    expect(outcome).toBe("failed");
    expect(result.current.state).toBe("error");
    expect(result.current.error).toBe("Disk full");

    let release: () => void = () => {};
    const slow = new Promise<void>((r) => (release = r));
    let first: Promise<string> = Promise.resolve("");
    await act(async () => {
      first = result.current.track(() => slow);
      await result.current.track(() => Promise.resolve());
    });
    await act(async () => {
      release();
      expect(await first).toBe("stale");
    });
    expect(result.current.state).toBe("saved");
  });
});
