import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactElement } from "react";

import { DocDriftReferences } from "../../src/doc-drift/doc-drift-references.js";
import type { DocDriftReferencesResponse } from "@repowise-dev/types/doc-drift";

const BASIS = "A reference names a file; it is not a description of it.";

function response(over: Partial<DocDriftReferencesResponse> = {}): DocDriftReferencesResponse {
  return {
    target_path: "src/auth.py",
    references: [
      { document: "docs/guide.md", line: 30, kind: "path", section: "Setup > Auth" },
      { document: "README.md", line: 4, kind: "path" },
      { document: "docs/guide.md", line: 12, kind: "path" },
    ],
    references_emitted: 3,
    references_total: 3,
    documents: 2,
    documents_with_drift: [{ document: "docs/guide.md", findings: 2 }],
    references_basis: BASIS,
    unavailable: null,
    ...over,
  };
}

type Adapter = React.ComponentProps<typeof DocDriftReferences>["adapter"];

function renderPanel(
  listReferences: Adapter["listReferences"],
  extra: { driftHref?: (document: string) => string; navigate?: (href: string) => void } = {},
): ReturnType<typeof render> {
  const { driftHref, navigate } = extra;
  const adapter: Adapter = {
    cacheKey: "repo-1",
    ...(listReferences ? { listReferences } : {}),
    documentHref: (path, line) => `/files/${path}${line ? `#L${line}` : ""}`,
    ...(navigate ? { navigate } : {}),
  };
  const node: ReactElement = (
    <DocDriftReferences target="src/auth.py" adapter={adapter} driftHref={driftHref} />
  );
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{node}</SWRConfig>,
  );
}

describe("DocDriftReferences", () => {
  it("lists each naming document once, with its lines and section", async () => {
    const load = vi.fn(async () => response());
    renderPanel(load);

    expect(await screen.findByText("docs/guide.md")).toBeTruthy();
    expect(load).toHaveBeenCalledWith("src/auth.py");
    expect(screen.getByText("README.md")).toBeTruthy();
    const lines = screen.getAllByText(/^line \d+$/).map((el) => el.textContent);
    expect(lines).toEqual(["line 12", "line 30", "line 4"]);
    expect(screen.getByText("Setup > Auth")).toBeTruthy();
    expect(screen.getByText(new RegExp(BASIS.slice(0, 30)))).toBeTruthy();
    // A reference is weaker than a description; the panel never claims more.
    expect(document.body.textContent).not.toMatch(/describes/i);
  });

  it("marks the documents that carry drift and links to their findings", async () => {
    const navigate = vi.fn();
    renderPanel(async () => response(), {
      driftHref: (d) => `/drift?document=${d}`,
      navigate,
    });

    fireEvent.click(await screen.findByText("2 drifted assertions in this document"));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/drift?document=docs/guide.md"));
    expect(screen.getAllByText(/drifted assertion/)).toHaveLength(1);
  });

  it("says plainly when no document names the file, with the basis", async () => {
    renderPanel(async () =>
      response({
        references: [],
        references_emitted: 0,
        references_total: 0,
        documents: 0,
        documents_with_drift: [],
      }),
    );
    expect(await screen.findByText(/No document names this file\./)).toBeTruthy();
    expect(screen.getByText(new RegExp(BASIS.slice(0, 30)))).toBeTruthy();
  });

  it("names the refusal rather than reading as no references", async () => {
    renderPanel(async () =>
      response({ references: [], documents: 0, unavailable: "not_computed" }),
    );
    expect(await screen.findByText(/has not been checked yet/i)).toBeTruthy();
    expect(screen.queryByText(/No document names this file/)).toBeNull();
  });

  it("renders nothing on a failed read", async () => {
    const load = vi.fn(async () => {
      throw new Error("boom");
    });
    const { container } = renderPanel(load);
    await waitFor(() => expect(load).toHaveBeenCalled());
    await waitFor(() => expect(container.textContent).toBe(""));
  });

  it("renders nothing when the host cannot list references", () => {
    const { container } = renderPanel(undefined);
    expect(container.textContent).toBe("");
  });

  it("leaves a modified or non-primary click to the browser", async () => {
    const navigate = vi.fn();
    renderPanel(async () => response(), { navigate });
    const link = await screen.findByText("README.md");
    // Runs after React's handler; stops jsdom attempting the real navigation.
    const block = (e: Event) => e.preventDefault();
    document.addEventListener("click", block);
    fireEvent.click(link, { button: 1 });
    fireEvent.click(link, { ctrlKey: true });
    expect(navigate).not.toHaveBeenCalled();
    fireEvent.click(link);
    expect(navigate).toHaveBeenCalledWith("/files/README.md");
    document.removeEventListener("click", block);
  });
});
