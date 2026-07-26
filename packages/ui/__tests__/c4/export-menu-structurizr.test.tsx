/**
 * The Structurizr entry in the C4 export menu.
 *
 * It downloads rather than copies: the DSL is a file people commit, and it
 * carries a header comment that only survives as a file. The entry is hidden
 * unless the host supplies a fetcher, matching how Mermaid and JSON behave.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/react";
import { C4ExportMenu } from "../../src/c4/export/ExportMenu";

const DSL = '# Structurizr DSL model for demo\nmodel {\n    sys_demo = softwareSystem "demo"\n}\n';

let createdUrls: string[] = [];
let lastBlob: Blob | null = null;

beforeEach(() => {
  createdUrls = [];
  lastBlob = null;
  // jsdom implements neither, and the exporter uses both to trigger a save.
  globalThis.URL.createObjectURL = vi.fn((blob: Blob) => {
    lastBlob = blob;
    const url = `blob:mock/${createdUrls.length}`;
    createdUrls.push(url);
    return url;
  }) as unknown as typeof URL.createObjectURL;
  globalThis.URL.revokeObjectURL = vi.fn() as unknown as typeof URL.revokeObjectURL;
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
  it("hides the entry when the host provides no fetcher", () => {
    const { container } = render(
      <C4ExportMenu nodes={[]} edges={[]} fileNameStem="demo" />,
    );
    open(container);
    expect(itemNamed(container, "Structurizr DSL")).toBeUndefined();
  });

  it("shows the entry when a fetcher is supplied", () => {
    const { container } = render(
      <C4ExportMenu
        nodes={[]}
        edges={[]}
        fileNameStem="demo"
        fetchStructurizr={async () => DSL}
      />,
    );
    open(container);
    expect(itemNamed(container, "Structurizr DSL")).toBeDefined();
  });

  it("downloads the DSL the host returns, unchanged", async () => {
    const fetchStructurizr = vi.fn(async () => DSL);
    const { container } = render(
      <C4ExportMenu
        nodes={[]}
        edges={[]}
        fileNameStem="demo"
        fetchStructurizr={fetchStructurizr}
      />,
    );
    open(container);
    fireEvent.click(itemNamed(container, "Structurizr DSL")!);

    await waitFor(() => expect(fetchStructurizr).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(createdUrls.length).toBe(1));
    expect(lastBlob).not.toBeNull();
    // The header comment is what tells a downloader what the file is, so the
    // bytes have to arrive exactly as the backend wrote them.
    await expect(readBlob(lastBlob!)).resolves.toBe(DSL);
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
    fireEvent.click(itemNamed(container, "Structurizr DSL")!);

    await waitFor(() =>
      expect(container.textContent).toContain("Export failed"),
    );
    expect(createdUrls.length).toBe(0);
  });
});
