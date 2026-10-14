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

    fireEvent.click(within(figure).getByRole("button", { name: "Data" }));

    expect(within(figure).getByRole("button", { name: "Hide data" })).toHaveAttribute("aria-expanded", "true");
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
