import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import manifest from "../../package.json" with { type: "json" };
import { FilePath } from "../../src/shared/file-path";
import { disambiguateBasenames } from "../../src/lib/format";

const LONG = "packages/server/src/repowise/server/mcp_server/tool_dependency.py";

describe("FilePath", () => {
  it("name-first puts the file name before the directory and keeps the full path as the name", () => {
    render(
      <a href="/f">
        <FilePath path={LONG} line={12} />
      </a>,
    );
    expect(screen.getByRole("link", { name: `${LONG}:12` })).toBeTruthy();
    const visual = screen.getByText("tool_dependency.py").closest("[aria-hidden]");
    expect(visual?.textContent?.startsWith("tool_dependency.py:12")).toBe(true);
  });

  it("folds the package prefix only when asked", () => {
    const { rerender } = render(<FilePath path={LONG} fold />);
    expect(screen.getByText("server")).toBeTruthy();
    expect(screen.getByText("mcp_server")).toBeTruthy();
    const visual = screen.getByText("tool_dependency.py").closest("[aria-hidden]");
    expect(visual?.textContent).not.toContain("repowise");
    rerender(<FilePath path={LONG} />);
    expect(screen.queryByText("server")).toBeNull();
  });

  it("distinguishing prints the shortest unique suffix", () => {
    const labels = disambiguateBasenames(["src/css/mod.rs", "src/text/mod.rs"]);
    render(<FilePath path="src/css/mod.rs" mode="distinguishing" labels={labels} />);
    const visual = screen.getByText("mod.rs").closest("[aria-hidden]");
    expect(visual?.textContent).toBe("css/mod.rs");
    expect(screen.getByText("src/css/mod.rs").className).toContain("sr-only");
  });

  it("full wraps only after slashes and has no hidden copy", () => {
    const { container } = render(<FilePath path="src/app/server.py" mode="full" />);
    expect(container.textContent).toBe("src/app/server.py");
    expect(container.querySelectorAll("wbr").length).toBe(2);
    expect(container.querySelector(".sr-only")).toBeNull();
  });

  it("does not duplicate a top-level file", () => {
    render(<FilePath path="README.md" />);
    expect(screen.getAllByText("README.md")).toHaveLength(1);
  });

  it("never adds a tab stop of its own", () => {
    const { container } = render(
      <>
        <a href="/x">
          <FilePath path="a/b.ts" />
        </a>
        <FilePath path="c/d.ts" />
      </>,
    );
    const triggers = container.querySelectorAll("[data-path] > span");
    expect(triggers[0]?.getAttribute("tabindex")).toBeNull();
    expect(triggers[1]?.getAttribute("tabindex")).toBeNull();
  });

  it("opens the tooltip when the holding link takes keyboard focus", async () => {
    render(
      <a href="/x">
        <FilePath path="a/b.ts" />
      </a>,
    );
    act(() => {
      screen.getByRole("link").focus();
    });
    expect((await screen.findByRole("tooltip")).textContent).toContain("a/b.ts");
  });

  it("mounts no tooltip machinery until the path is hovered", () => {
    const { container } = render(<FilePath path="a/b.ts" />);
    expect(container.querySelector("[data-state]")).toBeNull();
    fireEvent.pointerEnter(container.querySelector("[data-path] > span")!);
    expect(container.querySelector("[data-state]")).not.toBeNull();
  });

  it("opens on the first hover even when the pointer then stays still", async () => {
    vi.useFakeTimers();
    try {
      const { container } = render(<FilePath path="a/b.ts" />);
      fireEvent.pointerEnter(container.querySelector("[data-path] > span")!);
      await act(async () => {
        vi.advanceTimersByTime(300);
      });
      expect(screen.getByRole("tooltip").textContent).toContain("a/b.ts");
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not open when the first hover leaves before the delay", async () => {
    vi.useFakeTimers();
    try {
      const { container } = render(<FilePath path="a/b.ts" />);
      fireEvent.pointerEnter(container.querySelector("[data-path] > span")!);
      fireEvent.pointerLeave(container.querySelector("[data-path]")!);
      await act(async () => {
        vi.advanceTimersByTime(300);
      });
      expect(screen.queryByRole("tooltip")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it.each(["name-first", "middle-ellipsis", "distinguishing"] as const)("never truncates the file name in %s", (mode) => {
    render(<FilePath path={LONG} mode={mode} />);
    expect(screen.getByText("tool_dependency.py").className).not.toMatch(/\btruncate\b/);
  });

  it("keeps an inline mention on one line", () => {
    render(<FilePath path={LONG} mode="middle-ellipsis" />);
    expect(screen.getByText("tool_dependency.py").className).toMatch(/\bwhitespace-nowrap\b/);
  });

  it("copies the path", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<FilePath path="a/b.ts" mode="full" copyable copyLabel="Copy it" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy it" }));
    expect(writeText).toHaveBeenCalledWith("a/b.ts");
  });

  it("is published under the shared and lib subpaths", () => {
    const exports = manifest.exports as Record<string, string>;
    expect(exports["./shared/*"]).toBe("./src/shared/*.tsx");
    expect(exports["./lib/path-parts"]).toBe("./src/lib/path-parts.ts");
  });
});
