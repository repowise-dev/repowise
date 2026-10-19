import { cloneElement, type ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ChatUIMessage } from "@repowise-dev/types/chat";
import type { ChartView, SectionView, TableView, ViewPart } from "@repowise-dev/types/views";
import fixture from "../../../types/fixtures/views.conformance.json";
import { ViewPartList } from "../../src/chat/view-part.js";
import { ChatMessage } from "../../src/chat/chat-message.js";

// jsdom has no layout, so a ResponsiveContainer never measures. Give the plot
// a fixed box so the chart draws its SVG.
vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactElement<{ width?: number; height?: number }> }) =>
      cloneElement(children, { width: 480, height: 220 }),
  };
});

const valid = fixture.valid as unknown as Record<string, ViewPart>;
const TABLE = valid.t1 as TableView;
const BAR = valid.c1 as ChartView;
const STACKED = valid.c2 as ChartView;
const LINE = valid.c3 as ChartView;
const DONUT = valid.c4 as ChartView;
const SCATTER = valid.c5 as ChartView;
const SECTION = valid.s1 as SectionView;

const href = (ref: { type: string; id: string }) => `/repos/r1/${ref.type}/${ref.id}`;

function part(id: string) {
  return document.querySelector(`[data-view-id="${id}"]`) as HTMLElement;
}

describe("ViewPartList", () => {
  it("draws a chart as SVG with its title and summary, its table closed", () => {
    render(<ViewPartList views={[BAR]} buildRefHref={href} />);

    const figure = part("c1");
    expect(figure).toHaveAttribute("data-chart-type", "bar");
    expect(figure.querySelector("figcaption")).toHaveTextContent("Changes by component");
    expect(figure.querySelector("figcaption")).toHaveTextContent(BAR.summary);
    expect(figure.querySelector("svg.recharts-surface")).not.toBeNull();

    const toggle = within(figure).getByRole("button", { name: "Data" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    const data = figure.querySelector("[data-view-data]") as HTMLElement;
    expect(toggle).toHaveAttribute("aria-controls", data.id);
    expect(data).toHaveClass("hidden");
  });

  it("opens the plotted table from the data toggle, refs as links", () => {
    render(<ViewPartList views={[BAR]} buildRefHref={href} />);
    const figure = part("c1");

    const toggle = within(figure).getByRole("button", { name: "Data" });
    fireEvent.click(toggle);

    // The label stays put; aria-expanded carries the state.
    expect(toggle).toHaveTextContent(/^Data$/);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const data = figure.querySelector("[data-view-data]") as HTMLElement;
    expect(data).not.toHaveClass("hidden");
    const table = within(data).getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(within(table).getByRole("link", { name: "payments" })).toHaveAttribute(
      "href",
      "/repos/r1/component/payments",
    );
    expect(within(table).getByText("12")).toHaveClass("tabular-nums");
  });

  it("keeps the toggle off paper and prints every chart with its table", () => {
    render(<ViewPartList views={[BAR]} />);
    const figure = part("c1");

    expect(within(figure).getByRole("button", { name: "Data" })).toHaveClass("print:hidden");
    expect(figure.querySelector("[data-view-data]")).toHaveClass("print:block");
    expect(figure).toHaveClass("print:break-inside-avoid");
  });

  it("shows basis, coverage, truncation and the index commit under a table", () => {
    render(<ViewPartList views={[TABLE]} buildRefHref={href} />);
    const figure = part("t1");
    const line = figure.querySelector("[data-view-provenance]") as HTMLElement;

    expect(line).toHaveTextContent("Measured · git history");
    expect(line).toHaveTextContent("get_health");
    expect(line).toHaveTextContent("Test reach coverage 75%");
    expect(line).toHaveTextContent("Showing 2 of 3 rows");
    expect(line).toHaveTextContent("as of 4c1e31f");
    expect(line.querySelector("[data-view-stale]")).toBeNull();
    // A table part is its own data: no toggle.
    expect(within(figure).queryByRole("button")).toBeNull();
    expect(within(figure).getByRole("columnheader", { name: "Health (score)" })).toBeInTheDocument();
    expect(within(figure).getByRole("columnheader", { name: "Commits, 90 days" })).toBeInTheDocument();
    expect(within(figure).getByText("none")).toHaveClass("sr-only");
  });

  it("names every series in a legend and flags a stale index", () => {
    render(<ViewPartList views={[STACKED, LINE]} />);

    expect(within(part("c2")).getByRole("list")).toHaveTextContent("Measured");
    expect(within(part("c2")).getByRole("list")).toHaveTextContent("Inferred");
    expect(part("c3").querySelector("[data-view-stale]")).toHaveTextContent(
      "Index behind the code it read",
    );
  });

  it("plots five series and points to the data table for the rest", () => {
    const keys = ["s1", "s2", "s3", "s4", "s5", "s6", "s7"];
    const { sort: _sort, ...unsorted } = BAR;
    const wide: ChartView = {
      ...unsorted,
      id: "w1",
      data: {
        columns: [
          { key: "component", label: "Component" },
          ...keys.map((key) => ({ key, label: key.toUpperCase(), unit: "changes" })),
        ],
        rows: [
          { component: "auth", ...Object.fromEntries(keys.map((k, i) => [k, i + 1])) },
          { component: "search", ...Object.fromEntries(keys.map((k, i) => [k, i + 2])) },
        ],
        total_rows: 2,
        truncated: false,
      },
      series: keys,
    };
    render(<ViewPartList views={[wide]} />);
    const figure = part("w1");
    const legend = within(figure).getByRole("list");

    expect(within(legend).getAllByRole("listitem")).toHaveLength(6);
    expect(legend).toHaveTextContent("S5");
    expect(legend).not.toHaveTextContent("S6");
    expect(legend.querySelector("[data-view-undrawn]")).toHaveTextContent("2 more series in the data table");
    expect(figure.querySelectorAll(".recharts-bar")).toHaveLength(5);
    // The table keeps every series.
    expect(within(figure).getAllByRole("columnheader")).toHaveLength(8);
  });

  it("separates stacked segments by series colour and lines with dashes", () => {
    const lines: ChartView = { ...STACKED, id: "l2", chart_type: "line" };
    render(<ViewPartList views={[STACKED, lines]} />);

    const segmentFills = [...part("c2").querySelectorAll(".recharts-bar")].map((bar) =>
      bar.querySelector(".recharts-bar-rectangle path")?.getAttribute("fill"),
    );
    expect(segmentFills).toEqual(["var(--color-series-1)", "var(--color-series-2)"]);
    const dashes = [...part("l2").querySelectorAll(".recharts-line-curve")].map((el) =>
      el.getAttribute("stroke-dasharray"),
    );
    expect(dashes).toHaveLength(2);
    expect(new Set(dashes).size).toBe(2);
  });

  it("sits on the transcript without a card of its own", () => {
    render(<ViewPartList views={[STACKED]} />);
    const figure = part("c2");
    expect(figure.className).not.toMatch(/border|bg-\[/);
  });

  it("stacks a stacked bar's segments end to end on one row", () => {
    render(<ViewPartList views={[STACKED]} />);

    const [first, second] = [...part("c2").querySelectorAll(".recharts-bar")].map(
      (bar) => bar.querySelector(".recharts-bar-rectangle path") as SVGPathElement,
    );
    const start = (el: SVGPathElement) => Number(/^M\s*([\d.]+)/.exec(el.getAttribute("d") ?? "")?.[1]);
    const width = (el: SVGPathElement) => Number(el.getAttribute("width"));
    expect(start(second!)).toBeCloseTo(start(first!) + width(first!), 1);
  });

  it("plots a scatter's numeric points, skipping nulls, with the table one toggle away", () => {
    render(<ViewPartList views={[SCATTER]} />);
    const figure = part("c5");

    expect(figure).toHaveAttribute("data-chart-type", "scatter");
    // Two rows, one with a null y: one point drawn.
    expect(figure.querySelectorAll(".recharts-scatter-symbol")).toHaveLength(1);
    fireEvent.click(within(figure).getByRole("button", { name: "Data" }));
    expect(within(figure).getAllByRole("row")).toHaveLength(3);
  });

  it("gives each scatter series its own marker shape in the plot and legend", () => {
    const split: ChartView = {
      ...SCATTER,
      id: "sc2",
      data: {
        ...SCATTER.data,
        columns: [...SCATTER.data.columns, { key: "fixes", label: "Fixes" }],
        rows: SCATTER.data.rows.map((row, i) => ({ ...row, commits: i === 0 ? 41 : null, fixes: i === 1 ? 6 : null })),
      },
      series: ["commits", "fixes"],
    };
    render(<ViewPartList views={[split]} />);
    const figure = part("sc2");

    const shapes = [...figure.querySelectorAll(".recharts-scatter-symbol path")].map((el) => el.getAttribute("d"));
    expect(shapes).toHaveLength(2);
    expect(new Set(shapes).size).toBe(2);
    const legend = within(figure).getByRole("list");
    expect(within(legend).getAllByRole("listitem")).toHaveLength(2);
    expect(legend.querySelectorAll("svg path")).toHaveLength(2);
  });

  it("draws nothing for a donut with no series instead of failing", () => {
    render(<ViewPartList views={[{ ...DONUT, id: "d0", series: [] }]} />);

    expect(part("d0").querySelector("svg.recharts-surface")).toBeNull();
    expect(within(part("d0")).queryByRole("list")).toBeNull();
  });

  it("labels each donut part with its value", () => {
    render(<ViewPartList views={[DONUT]} />);
    const items = within(part("c4")).getAllByRole("listitem");

    expect(items).toHaveLength(DONUT.data.rows.length);
    const [first] = DONUT.data.rows;
    expect(items[0]).toHaveTextContent(String(first![DONUT.series[0]!]));
  });

  it("renders a section's parts inside it only, with narrative citing them", () => {
    render(<ViewPartList views={[TABLE, BAR, SECTION]} />);

    const section = part("s1");
    expect(section.tagName).toBe("SECTION");
    expect(within(section).getByRole("heading", { name: SECTION.title })).toBeInTheDocument();
    expect(document.querySelectorAll('[data-view-id="t1"]')).toHaveLength(1);
    expect(document.querySelectorAll('[data-view-id="c1"]')).toHaveLength(1);
    expect(section.contains(part("c1"))).toBe(true);
    const cite = within(section).getByRole("link", { name: `See ${BAR.title}` });
    expect(cite.getAttribute("href")).toBe(`#${part("c1").id}`);
  });

  it("nests a treemap by its groups and colours its cells by health band", () => {
    const TREEMAP = valid.c6 as ChartView;
    render(<ViewPartList views={[TREEMAP]} />);
    const figure = part("c6");

    expect(figure).toHaveAttribute("data-chart-type", "treemap");
    // A zero-sized row draws no cell; two packages hold the rest.
    const cells = figure.querySelectorAll("[data-treemap-cell]");
    expect(cells).toHaveLength(2);
    expect(figure.querySelectorAll("[data-treemap-group]").length).toBeGreaterThanOrEqual(1);
    expect((cells[0] as HTMLElement).style.background).toBe("var(--color-node-good)");
    // The band is named on the cell and in its title, not only coloured.
    expect(cells[0]).toHaveAttribute("title", expect.stringContaining("good"));
    const legend = within(figure).getByRole("list");
    expect(legend).toHaveTextContent("at risk");
    expect(legend).toHaveTextContent("good");
  });

  it("draws a heatmap as a grid with every value in its cell", () => {
    const HEATMAP = valid.c7 as ChartView;
    render(<ViewPartList views={[HEATMAP]} />);
    const figure = part("c7");

    const cells = [...figure.querySelectorAll("[data-heat-cell]")] as HTMLElement[];
    expect(cells.map((c) => c.textContent)).toEqual(["4", "1", "6"]);
    // The largest value is the darkest step and carries card-coloured text.
    expect(cells[2]!.style.color).toBe("var(--color-bg-surface)");
    expect(cells[1]!.style.color).toBe("var(--color-text-primary)");
    expect(within(figure).getByRole("list")).toHaveTextContent("6 (darkest)");
    // A table to assistive tech: a header row, then one row per week.
    const grid = figure.querySelector('[role="table"]') as HTMLElement;
    expect(grid).toHaveAttribute("aria-label", HEATMAP.title);
    expect(within(grid).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Week", "fix", "feature"]);
    expect(within(grid).getAllByRole("rowheader").map((h) => h.textContent)).toEqual(["2026-09-28", "2026-10-05"]);
    expect(within(grid).getAllByRole("cell")).toHaveLength(4);
    expect(within(grid).getByText("none")).toHaveClass("sr-only");
  });

  it("renders parts stored at schema version 1 and at the current one", () => {
    expect(BAR.schema_version).toBe(1);
    render(<ViewPartList views={[BAR, { ...BAR, id: "v2part", schema_version: 2 }]} />);
    expect(part("c1").querySelector("svg.recharts-surface")).not.toBeNull();
    expect(part("v2part").querySelector("svg.recharts-surface")).not.toBeNull();
  });

  it("fills an area chart and runs bars over dates as columns", () => {
    const weeks = ["2026-09-28", "2026-10-05", "2026-10-12"];
    const dated = { ...LINE.data, rows: LINE.data.rows.map((r, i) => ({ ...r, week: weeks[i]! })) };
    render(
      <ViewPartList
        views={[
          { ...LINE, id: "a1", chart_type: "area" },
          { ...LINE, id: "b1", chart_type: "bar", data: dated },
          { ...LINE, id: "h1", chart_type: "horizontal_bar", data: dated },
        ]}
      />,
    );

    expect(part("a1").querySelector(".recharts-area-area")).not.toBeNull();
    // Columns put the dates on the x axis; a horizontal bar puts them on y.
    expect(part("b1").querySelector(".recharts-xAxis-tick-labels")).toHaveTextContent("2026-09-28");
    expect(part("h1").querySelector(".recharts-yAxis-tick-labels")).toHaveTextContent("2026-09-28");
  });

  it("folds up to eight donut parts, each named with its value", () => {
    const rows = Array.from({ length: 8 }, (_, i) => ({ tier: `t${i}`, files: i + 1 }));
    render(
      <ViewPartList views={[{ ...DONUT, id: "d8", data: { ...DONUT.data, rows, total_rows: 8 } }]} />,
    );
    const items = within(part("d8")).getAllByRole("listitem");

    expect(items).toHaveLength(8);
    expect(items[7]).toHaveTextContent("t7");
    expect(items[7]).toHaveTextContent("8");
  });

  it("labels two files of one name by their shortest distinct path", () => {
    const rows = [
      { component: { type: "file" as const, id: "a/update_cmd/command.py" }, changes: 3 },
      { component: { type: "file" as const, id: "a/init_cmd/command.py" }, changes: 2 },
    ];
    const { sort: _sort, ...unsorted } = BAR;
    const data = { ...BAR.data, rows, total_rows: 2 };
    render(<ViewPartList views={[{ ...unsorted, id: "u1", chart_type: "donut", data }]} />);

    const legend = within(part("u1")).getByRole("list");
    expect(legend).toHaveTextContent("update_cmd/command.py");
    expect(legend).toHaveTextContent("init_cmd/command.py");
  });

  it("names a view tool's chart by its basis alone, and never says unresolved", () => {
    const fromChart: ChartView = {
      ...BAR,
      id: "p1",
      source: { ...BAR.source, tool_name: "chart" },
    };
    const unresolved: ChartView = {
      ...BAR,
      id: "p2",
      basis: { kind: "unresolved", source: "get_overview" },
      source: { ...BAR.source, tool_name: "get_overview" },
    };
    render(<ViewPartList views={[fromChart, unresolved]} />);

    expect(part("p1").querySelector("[data-view-provenance]")).not.toHaveTextContent("chart");
    const line = part("p2").querySelector("[data-view-provenance]") as HTMLElement;
    expect(line).toHaveTextContent("From get_overview");
    expect(line).not.toHaveTextContent(/unresolved/i);
  });

  it("gives repeated view ids page-unique anchors across turns", () => {
    render(
      <>
        <ViewPartList views={[BAR]} />
        <ViewPartList views={[BAR]} />
      </>,
    );
    const ids = [...document.querySelectorAll('[data-view-id="c1"]')].map((el) => el.id);
    expect(new Set(ids).size).toBe(2);
  });
});

describe("ChatMessage views", () => {
  const message: ChatUIMessage = {
    id: "a1",
    role: "assistant",
    text: "Payments changed most.",
    toolCalls: [],
    isStreaming: true,
    views: [BAR],
  };

  it("renders a view while the answer is still streaming", () => {
    render(<ChatMessage message={message} repoId="r1" />);

    expect(part("c1")).toBeInTheDocument();
    expect(screen.getByText("Payments changed most.")).toBeInTheDocument();
  });

  it("links ref cells to the dashboard", () => {
    render(<ChatMessage message={{ ...message, isStreaming: false }} repoId="r1" />);
    fireEvent.click(screen.getByRole("button", { name: "Data" }));

    // Components have no dashboard page, so the ref stays text.
    const table = within(part("c1")).getByRole("table");
    expect(within(table).queryByRole("link")).toBeNull();
    expect(within(table).getByText("payments")).toBeInTheDocument();
  });
});
