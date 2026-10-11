import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  ResponsiveTable,
  type ResponsiveColumn,
} from "../../src/shared/responsive-table/responsive-table.js";

interface Row {
  id: string;
  name: string;
  count: number;
}

const ROWS: Row[] = [
  { id: "a", name: "alpha", count: 3 },
  { id: "b", name: "beta", count: 7 },
];

const COLUMNS: ResponsiveColumn<Row>[] = [
  { key: "name", header: "Name", render: (r) => r.name },
  { key: "count", header: "Count", sortable: true, render: (r) => r.count },
];

describe("ResponsiveTable", () => {
  it("renders headers, rows, and a screen-reader caption", () => {
    render(
      <ResponsiveTable
        columns={COLUMNS}
        rows={ROWS}
        rowKey={(r) => r.id}
        caption="Test rows"
      />,
    );
    expect(screen.getByText("Name")).toBeTruthy();
    expect(screen.getByText("alpha")).toBeTruthy();
    expect(screen.getByText("beta")).toBeTruthy();
    expect(screen.getByText("Test rows").tagName).toBe("CAPTION");
  });

  it("renders the empty slot when there are no rows", () => {
    render(
      <ResponsiveTable
        columns={COLUMNS}
        rows={[]}
        rowKey={(r) => r.id}
        empty={<div>Nothing here</div>}
      />,
    );
    expect(screen.getByText("Nothing here")).toBeTruthy();
  });

  it("makes clickable rows focusable and activatable with Enter and Space", () => {
    const onRowClick = vi.fn();
    render(
      <ResponsiveTable
        columns={COLUMNS}
        rows={ROWS}
        rowKey={(r) => r.id}
        onRowClick={onRowClick}
      />,
    );
    const row = screen.getByText("alpha").closest("tr");
    expect(row).toBeTruthy();
    expect(row!.tabIndex).toBe(0);

    fireEvent.keyDown(row!, { key: "Enter" });
    expect(onRowClick).toHaveBeenCalledWith(ROWS[0]);

    fireEvent.keyDown(row!, { key: " " });
    expect(onRowClick).toHaveBeenCalledTimes(2);

    fireEvent.click(row!);
    expect(onRowClick).toHaveBeenCalledTimes(3);
  });

  it("does not activate the row when a key lands on an interactive child", () => {
    const onRowClick = vi.fn();
    render(
      <ResponsiveTable
        columns={[
          {
            key: "action",
            header: "Action",
            render: () => <button type="button">act</button>,
          },
        ]}
        rows={ROWS}
        rowKey={(r) => r.id}
        onRowClick={onRowClick}
      />,
    );
    const button = screen.getAllByText("act")[0]!;
    fireEvent.keyDown(button, { key: "Enter" });
    expect(onRowClick).not.toHaveBeenCalled();
  });

  it("does not make rows focusable without onRowClick", () => {
    render(<ResponsiveTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} />);
    const row = screen.getByText("alpha").closest("tr");
    expect(row!.tabIndex).toBe(-1);
  });

  it("exposes sortable headers as buttons and reports aria-sort", () => {
    const onSort = vi.fn();
    render(
      <ResponsiveTable
        columns={COLUMNS}
        rows={ROWS}
        rowKey={(r) => r.id}
        sortField="count"
        sortOrder="asc"
        onSort={onSort}
      />,
    );
    const header = screen.getByRole("button", { name: /count/i });
    fireEvent.click(header);
    expect(onSort).toHaveBeenCalledWith("count");
    expect(header.closest("th")!.getAttribute("aria-sort")).toBe("ascending");
  });
});
