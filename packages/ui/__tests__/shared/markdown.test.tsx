import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { Markdown } from "../../src/shared/markdown.js";

const codeToHtml = vi.hoisted(() => vi.fn(async () => '<pre class="shiki"><code><span>highlighted</span></code></pre>'));
vi.mock("shiki", () => ({ codeToHtml }));

describe("Markdown", () => {
  it("renders reply body at the reading scale, not chrome sizes", () => {
    // Chat is a reading surface: a reply is read for minutes, so body is 16px.
    // It used to render at 14px secondary because the surface lives behind a
    // control row in the app shell.
    render(<Markdown content="A plain paragraph." />);
    const p = screen.getByText("A plain paragraph.");
    expect(p.className).toContain("text-base");
    expect(p.className).toContain("text-[var(--color-text-primary)]");
  });

  it("separates headings from body by size, not weight alone", () => {
    // h1/h2/h3 previously landed at 16/14/14 against 14px body, so an h2 was
    // the same size as the text under it — bolded body, not a heading.
    render(<Markdown content={"# One\n\n## Two\n\n### Three\n\nBody."} />);
    expect(screen.getByRole("heading", { level: 1 }).className).toContain(
      "text-2xl",
    );
    expect(screen.getByRole("heading", { level: 2 }).className).toContain(
      "text-xl",
    );
    expect(screen.getByRole("heading", { level: 3 }).className).toContain(
      "text-lg",
    );
    expect(screen.getByText("Body.").className).toContain("text-base");
  });

  it("never renders inline code in the accent", () => {
    // Rule 9: nothing in a reply resolves to a page, so an accent path would
    // decorate something that does not respond. This is the exact wall-of-orange
    // the wiki renderer already fixed.
    const { container } = render(
      <Markdown content="Look at `src/auth/session.py` for this." />,
    );
    const code = container.querySelector("code");
    expect(code).not.toBeNull();
    expect(code!.className).not.toContain("accent");
    expect(code!.className).toContain("font-mono");
  });

  it("drops to chrome sizes under density=compact", () => {
    // The narrow graph doc panel shares this renderer; its column cannot pay
    // for 16px body. One component with a prop, not a second renderer.
    render(<Markdown content="Panel text." density="compact" />);
    expect(screen.getByText("Panel text.").className).toContain("text-xs");
  });

  it("gives table headers the mono micro-label treatment", () => {
    const { container } = render(
      <Markdown content={"| File | Score |\n|---|---|\n| a.py | 4 |"} />,
    );
    const th = container.querySelector("th");
    expect(th).not.toBeNull();
    expect(th!.className).toContain("font-mono");
    expect(th!.className).toContain("uppercase");
  });

  it("contains wide tables in their own responsive content lane", () => {
    const { container } = render(
      <Markdown content={"| File | Hash |\n|---|---|\n| src/a.ts | abcdef |"} />,
    );
    const table = container.querySelector("table");
    expect(table?.className).toContain("min-w-");
    expect(table?.parentElement?.className).toContain("overflow-x-auto");
  });

  it("labels fenced code and exposes a copy action", () => {
    render(<Markdown content={"```ts\nconst answer = 42\n```"} streaming />);
    expect(screen.getByText("ts")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy code" })).toBeInTheDocument();
  });

  it("keeps language-less fences as copyable block code", () => {
    const { container } = render(<Markdown content={"```\nplain output\n```"} streaming />);
    expect(screen.getByText("text")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy code" })).toBeInTheDocument();
    expect(container.querySelector("pre code")?.textContent).toBe("plain output");
  });

  it("keeps incomplete streaming fences as a safe plain-code fallback", () => {
    const { container } = render(
      <Markdown content={"```ts\nconst unfinished ="} streaming />,
    );
    expect(container.querySelector("pre code")?.textContent).toContain(
      "const unfinished =",
    );
    expect(container.querySelector(".shiki")).toBeNull();
  });

  it("lazy-highlights completed code and reuses the cached result", async () => {
    const content = "```ts\nconst cached = true\n```";
    const first = render(<Markdown content={content} />);
    await waitFor(() => expect(first.container.querySelector(".shiki")).not.toBeNull());
    first.unmount();
    const second = render(<Markdown content={content} />);
    await waitFor(() => expect(second.container.querySelector(".shiki")).not.toBeNull());
    expect(codeToHtml).toHaveBeenCalledTimes(1);
  });

  it("keeps streaming Mermaid as stable source until the fence is complete", () => {
    const { container } = render(<Markdown content={"```mermaid\ngraph TD; A-->"} streaming />);
    expect(container.querySelector("pre code")?.textContent).toContain("graph TD; A-->");
  });

  it("preserves semantic nested lists, task lists, and blockquotes", () => {
    const { container } = render(<Markdown content={"> Note\n\n- parent\n  - child\n\n- [x] done"} />);
    expect(container.querySelector("blockquote")).not.toBeNull();
    expect(container.querySelectorAll("ul").length).toBeGreaterThan(1);
    expect(container.querySelector('input[type="checkbox"]')).toBeChecked();
  });
});
