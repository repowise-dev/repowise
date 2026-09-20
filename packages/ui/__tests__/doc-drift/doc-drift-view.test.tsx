import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactElement } from "react";

import { DocDriftView } from "../../src/doc-drift/doc-drift-view.js";
import type { DocDriftAdapter } from "../../src/doc-drift/doc-drift-adapter.js";
import type {
  DocDriftFinding,
  DocDriftResponse,
} from "@repowise-dev/types/doc-drift";

// jsdom has no layout engine; the shared table's virtualization path reads one.
class RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", RO);

/**
 * The findings table stacks into a card list beside the real table and lets CSS
 * pick one. jsdom applies no CSS, so a cell matches in both; scope by caption.
 */
const TABLE = { name: /Documentation assertions/i };
/** The detail panel and the prompt modal are both dialogs; name tells them apart. */
const PANEL = /docs\/architecture\.md:42/;
const PROMPT = /Documentation fix prompt/i;

const BASIS =
  "Covers only references this detector can resolve; uncheckable ones are neither counted nor reported.";

const FINDINGS: DocDriftFinding[] = [
  {
    id: "f1",
    file_path: "docs/architecture.md",
    line_number: 42,
    kind: "path",
    target: "src/auth.py",
    confidence: 0.9,
    origin: "path_no_candidate",
    reason: "No file matches this path.",
    raw: "src/auth.py",
    context: "The resolver lives in src/auth.py.",
    evidence: [
      "docs/architecture.md:42 states `src/auth.py`",
      "resolution: no-candidate",
      "under: Architecture > Resolvers",
    ],
  },
  {
    id: "f2",
    file_path: "docs/cli.md",
    line_number: 7,
    kind: "anchor",
    target: "docs/cli.md#usage",
    confidence: 0.5,
    origin: "anchor_no_heading",
    reason: "No heading slugs to this fragment.",
    raw: "#usage",
    context: "See [usage](#usage).",
    evidence: [],
  },
];

function response(over: Partial<DocDriftResponse> = {}): DocDriftResponse {
  return {
    findings: FINDINGS,
    findings_emitted: FINDINGS.length,
    summary: {
      findings_total: FINDINGS.length,
      documents: 2,
      confidence: { high: 1, medium: 1, low: 0 },
      by_kind: { anchor: 1, path: 1 },
      findings_basis: BASIS,
    },
    unavailable: null,
    ...over,
  };
}

/** The shape the engine returns when it will not answer: a cause, no summary. */
function refusal(reason: string): DocDriftResponse {
  return response({
    findings: [],
    findings_emitted: 0,
    summary: null,
    unavailable: reason as DocDriftResponse["unavailable"],
  });
}

function makeAdapter(over: Partial<DocDriftAdapter> = {}): DocDriftAdapter {
  return {
    cacheKey: "repo-1",
    listFindings: vi.fn(async () => response()),
    documentHref: (path, line) =>
      `/repos/repo-1/files/${path}${line ? `#L${line}` : ""}`,
    navigate: vi.fn(),
    ...over,
  };
}

function renderView(node: ReactElement) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      {node}
    </SWRConfig>,
  );
}

describe("DocDriftView", () => {
  it("leads with the count and names the documents to edit", async () => {
    renderView(<DocDriftView adapter={makeAdapter()} />);

    const table = await screen.findByRole("table", TABLE);
    expect(within(table).getByText(/docs\/architecture\.md/)).toBeTruthy();
    expect(within(table).getByText("src/auth.py")).toBeTruthy();
  });

  it("never shows a count without saying what it covers", async () => {
    // Most references in a real tree are uncheckable; a bare figure claims a
    // coverage this detector does not have.
    renderView(<DocDriftView adapter={makeAdapter()} />);
    expect(await screen.findByText(new RegExp(BASIS.slice(0, 40)))).toBeTruthy();
  });

  it("refuses rather than reporting an unchecked repository as clean", async () => {
    const adapter = makeAdapter({
      listFindings: vi.fn(async () =>
        refusal("not_computed"),
      ),
    });
    renderView(<DocDriftView adapter={adapter} />);

    expect(
      await screen.findByText(/has not been checked yet/i),
    ).toBeTruthy();
    // The one thing it must not render: an empty table reading as a clean tree.
    expect(screen.queryByRole("table", TABLE)).toBeNull();
    expect(screen.queryByText(/No documentation drift found/i)).toBeNull();
  });

  it("tells a stale index apart from a failed read", async () => {
    const adapter = makeAdapter({
      listFindings: vi.fn(async () =>
        refusal("drift_read_failed"),
      ),
    });
    renderView(<DocDriftView adapter={adapter} />);

    // "Reindex" is wrong advice for a locked database, so the copy must not
    // give it here.
    const message = await screen.findByText(/Re-indexing will not help/i);
    expect(message).toBeTruthy();
  });

  it("offers a way out of a transient read failure", async () => {
    // Nothing revalidates on focus, so without this the tab is stranded until
    // a full page reload.
    const listFindings = vi
      .fn()
      .mockResolvedValueOnce(
        refusal("drift_read_failed"),
      )
      .mockResolvedValue(response());
    renderView(<DocDriftView adapter={makeAdapter({ listFindings })} />);

    fireEvent.click(await screen.findByRole("button", { name: /Try again/i }));
    expect(await screen.findByRole("table", TABLE)).toBeTruthy();
  });

  it("does not offer a retry where retrying cannot change the answer", async () => {
    const adapter = makeAdapter({
      listFindings: vi.fn(async () =>
        refusal("index_predates_doc_drift"),
      ),
    });
    renderView(<DocDriftView adapter={adapter} />);

    await screen.findByText(/predates documentation drift/i);
    expect(screen.queryByRole("button", { name: /Try again/i })).toBeNull();
  });

  it("degrades rather than unmounting on a cause it does not know", async () => {
    const adapter = makeAdapter({
      listFindings: vi.fn(async () =>
        // A newer engine may grow the vocabulary; a hard lookup would take
          // the whole tab down with it.
          refusal("some_future_cause"),
      ),
    });
    renderView(<DocDriftView adapter={adapter} />);

    expect(
      await screen.findByText(/documentation drift is unavailable/i),
    ).toBeTruthy();
  });

  it("reports a genuinely clean repository as clean", async () => {
    const adapter = makeAdapter({
      listFindings: vi.fn(async () =>
        response({
          findings: [],
          findings_emitted: 0,
          summary: {
            findings_total: 0,
            documents: 0,
            confidence: { high: 0, medium: 0, low: 0 },
            by_kind: {},
            findings_basis: BASIS,
          },
        }),
      ),
    });
    renderView(<DocDriftView adapter={adapter} />);

    expect(await screen.findByText(/No documentation drift found/i)).toBeTruthy();
  });

  it("says so when the page is a slice of the repository", async () => {
    const adapter = makeAdapter({
      listFindings: vi.fn(async () =>
        response({
          findings_emitted: 2,
          summary: { ...response().summary!, findings_total: 40 },
        }),
      ),
    });
    renderView(<DocDriftView adapter={adapter} />);

    expect(await screen.findByText(/Showing 2 of 40 findings/i)).toBeTruthy();
  });

  it("narrows the summary with the rows, not just the rows", async () => {
    const listFindings = vi.fn(async (opts?: { min_confidence?: number }) =>
      opts?.min_confidence === 0.7
        ? response({
            findings: [FINDINGS[0]!],
            findings_emitted: 1,
            summary: {
              findings_total: 1,
              documents: 1,
              confidence: { high: 1, medium: 0, low: 0 },
              by_kind: { path: 1 },
              findings_basis: BASIS,
            },
          })
        : response(),
    );
    renderView(<DocDriftView adapter={makeAdapter({ listFindings })} />);

    await screen.findByRole("table", TABLE);
    fireEvent.change(screen.getByLabelText(/Minimum confidence/i), {
      target: { value: "0.7" },
    });

    await waitFor(() =>
      expect(listFindings).toHaveBeenCalledWith(
        expect.objectContaining({ min_confidence: 0.7 }),
      ),
    );
  });

  it("keeps the reference filter clearable after it is used", async () => {
    // A selected filter that erases its own alternatives is a dead end.
    renderView(<DocDriftView adapter={makeAdapter()} />);
    await screen.findByRole("table", TABLE);

    const select = screen.getByLabelText(/Reference class/i);
    fireEvent.change(select, { target: { value: "path" } });

    await waitFor(() => {
      const options = within(select as HTMLSelectElement).getAllByRole("option");
      expect(options.length).toBeGreaterThan(1);
    });
  });

  it("does not offer a reference filter a repository cannot use", async () => {
    const adapter = makeAdapter({
      listFindings: vi.fn(async () =>
        response({
          findings: [FINDINGS[0]!],
          findings_emitted: 1,
          summary: { ...response().summary!, by_kind: { path: 1 } },
        }),
      ),
    });
    renderView(<DocDriftView adapter={adapter} />);

    await screen.findByRole("table", TABLE);
    expect(screen.queryByLabelText(/Reference class/i)).toBeNull();
  });

  it("shows the failure rather than an empty list when the fetch fails", async () => {
    const adapter = makeAdapter({
      listFindings: vi.fn(async () => {
        throw new Error("boom");
      }),
    });
    renderView(<DocDriftView adapter={adapter} />);

    expect(
      await screen.findByText(/Couldn't load documentation drift/i),
    ).toBeTruthy();
  });

  it("lets a host supply its own failure card", async () => {
    const adapter = makeAdapter({
      listFindings: vi.fn(async () => {
        throw new Error("boom");
      }),
    });
    renderView(
      <DocDriftView
        adapter={adapter}
        renderError={() => <p>Sign in to see this</p>}
      />,
    );

    expect(await screen.findByText("Sign in to see this")).toBeTruthy();
  });

  it("opens the detail panel rather than navigating away", async () => {
    // The row used to jump to the document's file page, which for a markdown
    // file renders an empty shell: no symbols, no edges, none of the evidence.
    const navigate = vi.fn();
    renderView(<DocDriftView adapter={makeAdapter({ navigate })} />);

    const table = await screen.findByRole("table", TABLE);
    fireEvent.click(within(table).getByText(/docs\/architecture\.md/));

    expect(await screen.findByRole("dialog", { name: PANEL })).toBeTruthy();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("shows the evidence the row has no room for", async () => {
    renderView(<DocDriftView adapter={makeAdapter()} />);
    const table = await screen.findByRole("table", TABLE);
    fireEvent.click(within(table).getByText(/docs\/architecture\.md/));

    // The line as written, the heading trail and the resolver's trace all
    // arrive with every finding and were previously discarded.
    expect(await screen.findByText(/The resolver lives in src\/auth\.py\./)).toBeTruthy();
    expect(screen.getByText("Architecture > Resolvers")).toBeTruthy();
    expect(screen.getByText(/resolution: no-candidate/)).toBeTruthy();
  });

  it("repeats what a finding does and does not claim where the action is", async () => {
    renderView(<DocDriftView adapter={makeAdapter()} />);
    const table = await screen.findByRole("table", TABLE);
    fireEvent.click(within(table).getByText(/docs\/architecture\.md/));

    const panel = await screen.findByRole("dialog", { name: PANEL });
    expect(within(panel).getByText(new RegExp(BASIS.slice(0, 40)))).toBeTruthy();
  });

  it("reaches the document from the panel, not from the row", async () => {
    const navigate = vi.fn();
    renderView(<DocDriftView adapter={makeAdapter({ navigate })} />);
    const table = await screen.findByRole("table", TABLE);
    fireEvent.click(within(table).getByText(/docs\/architecture\.md/));

    fireEvent.click(await screen.findByRole("link", { name: /Open document/i }));
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith(
        "/repos/repo-1/files/docs/architecture.md#L42",
      ),
    );
  });

  it("hands one finding to an agent from its row", async () => {
    renderView(<DocDriftView adapter={makeAdapter()} />);
    const table = await screen.findByRole("table", TABLE);

    fireEvent.click(
      within(table).getByRole("button", {
        name: /Fix docs\/architecture\.md:42 with an agent/i,
      }),
    );

    expect(await screen.findByRole("dialog", { name: PROMPT })).toBeTruthy();
    // Only one: the row button stops propagation, so the row's own click
    // handler never runs and the detail panel stays shut.
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  it("builds a prompt naming the document as the thing to edit", async () => {
    renderView(<DocDriftView adapter={makeAdapter()} />);
    const table = await screen.findByRole("table", TABLE);
    fireEvent.click(
      within(table).getByRole("button", {
        name: /Fix docs\/architecture\.md:42 with an agent/i,
      }),
    );

    const prompt = (await screen.findByRole("dialog", { name: PROMPT })).textContent ?? "";
    // The direction of the claim is the one thing this prompt must not invert.
    expect(prompt).toContain("`docs/architecture.md:42` claims `src/auth.py` exists");
    expect(prompt).toContain("Edit the document, not the code.");
    // And it has to leave room for a deliberate example to be correct.
    expect(prompt).toContain("Some findings are correct as written.");
  });

  it("can hand the whole slice over at once", async () => {
    renderView(<DocDriftView adapter={makeAdapter()} />);
    await screen.findByRole("table", TABLE);

    fireEvent.click(screen.getByRole("button", { name: /Fix 2 with an agent/i }));
    const prompt = (await screen.findByRole("dialog", { name: PROMPT })).textContent ?? "";
    expect(prompt).toContain("docs/architecture.md:42");
    expect(prompt).toContain("docs/cli.md:7");
  });
});
