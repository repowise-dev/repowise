import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FixHistoryBadge } from "../../src/git/fix-history-badge.js";

const daysAgo = (n: number) =>
  new Date(Date.now() - n * 86_400_000).toISOString();

describe("FixHistoryBadge", () => {
  it("renders nothing for a file with no counted fixes", () => {
    const { container } = render(
      <FixHistoryBadge count={0} lastFixAt={daysAgo(2)} bugMagnet />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the count is absent entirely", () => {
    const { container } = render(<FixHistoryBadge count={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the count alongside the age of the last fix", () => {
    render(<FixHistoryBadge count={3} lastFixAt={daysAgo(2)} />);
    expect(screen.getByText("3 fixes · last 2d ago")).toBeTruthy();
  });

  it("calls out a bug magnet once an age anchors it", () => {
    render(<FixHistoryBadge count={4} lastFixAt={daysAgo(6)} bugMagnet />);
    expect(screen.getByText(/^Bug magnet ·/)).toBeTruthy();
  });

  it("hides the magnet wording when the timestamp is missing", () => {
    render(<FixHistoryBadge count={4} lastFixAt={null} bugMagnet />);
    expect(screen.getByText("4 fixes")).toBeTruthy();
    expect(screen.queryByText(/Bug magnet/)).toBeNull();
  });
});
