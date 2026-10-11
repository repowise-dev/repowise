import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { IndexStorageMini } from "../../src/dashboard/index-storage-mini";

describe("IndexStorageMini", () => {
  it("renders formatted storage and average doc confidence", () => {
    render(
      <IndexStorageMini
        data={{
          index_storage_bytes: 1_572_864,
          page_count: 42,
          doc_coverage_pct: 87.4,
        }}
      />,
    );

    expect(screen.getByText("Index storage")).toBeInTheDocument();
    expect(screen.getByText("1.5 MB")).toBeInTheDocument();
    expect(screen.getByText(/87% avg doc confidence/)).toBeInTheDocument();
  });
});
