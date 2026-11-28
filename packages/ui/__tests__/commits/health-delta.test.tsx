import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { healthDeltaKind } from "../../src/commits/health-delta";
import { HealthDeltaMark } from "../../src/commits/health-delta-mark";

const d = (introduced: number, worsened: number, resolved: number, improved?: number | null) => ({
  introduced,
  worsened,
  resolved,
  improved,
});

describe("healthDeltaKind", () => {
  it("reads the counts", () => {
    expect(healthDeltaKind(d(0, 0, 0))).toBe("neutral");
    expect(healthDeltaKind(d(0, 0, 2))).toBe("improved");
    expect(healthDeltaKind(d(0, 0, 0, 1))).toBe("improved");
    expect(healthDeltaKind(d(1, 0, 0))).toBe("worsened");
    expect(healthDeltaKind(d(0, 1, 3))).toBe("mixed");
  });

  it("prefers the server's own kind", () => {
    expect(healthDeltaKind({ ...d(0, 0, 0), kind: "mixed" })).toBe("mixed");
  });
});

describe("HealthDeltaMark", () => {
  it("says a change was not compared rather than reading it as clean", () => {
    render(<HealthDeltaMark delta={null} />);
    expect(screen.getByText("Not yet analysed")).toBeTruthy();
  });

  it("colours only a real improvement green and a regression red", () => {
    const { container, rerender } = render(<HealthDeltaMark delta={d(0, 0, 3)} />);
    expect(screen.getByText("+3")).toBeTruthy();
    expect(container.innerHTML).toContain("--color-success");

    rerender(<HealthDeltaMark delta={d(2, 0, 0)} />);
    expect(screen.getByText("\u22122")).toBeTruthy();
    expect(container.innerHTML).toContain("--color-error");

    rerender(<HealthDeltaMark delta={d(1, 0, 3)} />);
    expect(screen.getByText("Mixed")).toBeTruthy();
    expect(container.innerHTML).not.toMatch(/--color-(success|error)/);
  });

  it("renders a neutral result quietly, with no dot or figure", () => {
    const { container } = render(<HealthDeltaMark delta={d(0, 0, 0)} labels={{ neutral: "Unchanged" }} />);
    expect(screen.getByText("Unchanged")).toBeTruthy();
    expect(container.querySelector(".rounded-full")).toBeNull();
  });

  it("names a partial comparison", () => {
    render(<HealthDeltaMark delta={{ ...d(1, 0, 0), status: "partial" }} />);
    expect(screen.getByText("Part of this change was not compared")).toBeTruthy();
  });

  it("shows a stale delta's direction without colour or counts", () => {
    const { container } = render(<HealthDeltaMark delta={{ ...d(2, 0, 0), stale: true }} />);
    expect(screen.getByText("Worse")).toBeTruthy();
    expect(screen.queryByText("−2")).toBeNull();
    expect(container.innerHTML).not.toContain("--color-error");
    expect(screen.getByText(/older analyzer/)).toBeTruthy();
  });
});
