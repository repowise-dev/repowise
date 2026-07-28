/**
 * The Structurizr entries in the C4 export menu.
 *
 * They download rather than copy: the DSL is a file people commit, and it
 * carries a header comment that only survives as a file. The entries are
 * hidden unless the host supplies a fetcher, matching how Mermaid and JSON
 * behave.
 *
 * There are two of them because the two shapes are not interchangeable — a
 * fragment has no `workspace` block and will not open on its own — so the menu
 * has to let the user say which one they want rather than guessing.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/react";
import { C4ExportMenu } from "../../src/c4/export/ExportMenu";

const FRAGMENT =
  '# Structurizr DSL model for demo\nmodel {\n    sys_demo = softwareSystem "demo"\n}\n';
const WORKSPACE = `# Structurizr DSL model for demo\nworkspace "demo" {\n    ${FRAGMENT}\n}\n`;

const WORKSPACE_ITEM = "Structurizr workspace";
const FRAGMENT_ITEM = "Structurizr model fragment";

let createdUrls: string[] = [];
let lastBlob: Blob | null = null;
let lastDownloadName: string | null = null;

beforeEach(() => {
  createdUrls = [];
  lastBlob = null;
  lastDownloadName = null;
  // jsdom implements neither, and the exporter uses both to trigger a save.
  globalThis.URL.createObjectURL = vi.fn((blob: Blob) => {
    lastBlob = blob;
    const url = `blob:mock/${createdUrls.length}`;
    createdUrls.push(url);
    return url;
  }) as unknown as typeof URL.createObjectURL;
  globalThis.URL.revokeObjectURL = vi.fn() as unknown as typeof URL.revokeObjectURL;
  // The filename is half the point of the two entries — capture it off the
  // anchor the exporter clicks, since jsdom performs no actual download.
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    lastDownloadName = this.download;
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** jsdom's Blob has no .text(), so read it the way a browser would. */
function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

function open(container: HTMLElement) {
  fireEvent.click(container.querySelector("button")!);
}

function itemNamed(container: HTMLElement, label: string) {
  return Array.from(container.querySelectorAll('[role="menuitem"]')).find(
    (node) => node.textContent?.includes(label),
  );
}

describe("C4ExportMenu — Structurizr DSL", () => {
  it("hides the entries when the host provides no fetcher", () => {
    const { container } = render(
      <C4ExportMenu nodes={[]} edges={[]} fileNameStem="demo" />,
    );
    open(container);
    expect(itemNamed(container, WORKSPACE_ITEM)).toBeUndefined();
    expect(itemNamed(container, FRAGMENT_ITEM)).toBeUndefined();
  });

  it("offers both shapes when a fetcher is supplied", () => {
    const { container } = render(
      <C4ExportMenu
        nodes={[]}
        edges={[]}
        fileNameStem="demo"
        fetchStructurizr={async () => FRAGMENT}
      />,
    );
    open(container);
    expect(itemNamed(container, WORKSPACE_ITEM)).toBeDefined();
    expect(itemNamed(container, FRAGMENT_ITEM)).toBeDefined();
  });

  it("asks for a standalone workspace and names the file workspace.dsl", async () => {
    const fetchStructurizr = vi.fn(async () => WORKSPACE);
    const { container } = render(
      <C4ExportMenu
        nodes={[]}
        edges={[]}
        fileNameStem="demo"
        fetchStructurizr={fetchStructurizr}
      />,
    );
    open(container);
    fireEvent.click(itemNamed(container, WORKSPACE_ITEM)!);

    await waitFor(() =>
      expect(fetchStructurizr).toHaveBeenCalledWith({ standalone: true }),
    );
    await waitFor(() => expect(createdUrls.length).toBe(1));
    expect(lastDownloadName).toBe("workspace.dsl");
    // The header comment is what tells a downloader what the file is, so the
    // bytes have to arrive exactly as the backend wrote them.
    await expect(readBlob(lastBlob!)).resolves.toBe(WORKSPACE);
  });

  it("asks for a fragment and names the file repowise-model.dsl", async () => {
    const fetchStructurizr = vi.fn(async () => FRAGMENT);
    const { container } = render(
      <C4ExportMenu
        nodes={[]}
        edges={[]}
        fileNameStem="demo"
        fetchStructurizr={fetchStructurizr}
      />,
    );
    open(container);
    fireEvent.click(itemNamed(container, FRAGMENT_ITEM)!);

    await waitFor(() =>
      expect(fetchStructurizr).toHaveBeenCalledWith({ standalone: false }),
    );
    await waitFor(() => expect(createdUrls.length).toBe(1));
    expect(lastDownloadName).toBe("repowise-model.dsl");
    await expect(readBlob(lastBlob!)).resolves.toBe(FRAGMENT);
  });

  it("puts the openable file first", () => {
    const { container } = render(
      <C4ExportMenu
        nodes={[]}
        edges={[]}
        fileNameStem="demo"
        fetchStructurizr={async () => WORKSPACE}
      />,
    );
    open(container);
    const labels = Array.from(container.querySelectorAll('[role="menuitem"]')).map(
      (node) => node.textContent ?? "",
    );
    const workspaceAt = labels.findIndex((l) => l.includes(WORKSPACE_ITEM));
    const fragmentAt = labels.findIndex((l) => l.includes(FRAGMENT_ITEM));
    expect(workspaceAt).toBeGreaterThan(-1);
    expect(workspaceAt).toBeLessThan(fragmentAt);
  });

  it("says what the fragment needs, so nobody downloads it by mistake", () => {
    const { container } = render(
      <C4ExportMenu
        nodes={[]}
        edges={[]}
        fileNameStem="demo"
        fetchStructurizr={async () => FRAGMENT}
      />,
    );
    open(container);
    expect(itemNamed(container, FRAGMENT_ITEM)?.textContent).toContain("workspace.dsl");
  });

  it("reports a failure instead of failing silently", async () => {
    const { container } = render(
      <C4ExportMenu
        nodes={[]}
        edges={[]}
        fileNameStem="demo"
        fetchStructurizr={async () => {
          throw new Error("nope");
        }}
      />,
    );
    open(container);
    fireEvent.click(itemNamed(container, WORKSPACE_ITEM)!);

    await waitFor(() =>
      expect(container.textContent).toContain("Export failed"),
    );
    expect(createdUrls.length).toBe(0);
  });
});
