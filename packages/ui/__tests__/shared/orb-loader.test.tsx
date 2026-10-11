import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// The orb paints a 2D canvas, which jsdom does not implement. The wrapper's
// job is role, label, size, state and theme, so the canvas is stubbed.
vi.mock("thinking-orbs", () => ({
  ThinkingOrb: (props: { state?: string; size?: number; theme?: string }) => (
    <canvas
      data-testid="orb"
      data-state={props.state}
      data-size={props.size}
      data-theme={props.theme}
    />
  ),
}));

let resolvedTheme: string | undefined = "dark";
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme }) }));

import { ORB_STATE, OrbLoader } from "../../src/shared/orb-loader";
import { OwlLoader } from "../../src/shared/owl-loader";
import { WorkingOrb } from "../../src/chat/working-orb";

describe("OrbLoader", () => {
  it("is a labelled status with a visible caption at the 64px region size", () => {
    render(<OrbLoader label="Loading layers" />);
    expect(screen.getByRole("status", { name: "Loading layers" })).toBeInTheDocument();
    expect(screen.getByText("Loading layers")).toBeInTheDocument();
    const orb = screen.getByTestId("orb");
    expect(orb).toHaveAttribute("data-size", "64");
    expect(orb).toHaveAttribute("data-state", "composing");
  });

  it("inline size hides the caption and defaults to working", () => {
    render(<OrbLoader size={20} label="Reading the index" />);
    expect(screen.getByRole("status", { name: "Reading the index" })).toBeInTheDocument();
    expect(screen.queryByText("Reading the index")).not.toBeInTheDocument();
    expect(screen.getByTestId("orb")).toHaveAttribute("data-state", "working");
  });

  it("takes a state from the shared mapping", () => {
    render(<OrbLoader state={ORB_STATE.tracing} />);
    expect(screen.getByTestId("orb")).toHaveAttribute("data-state", "connecting");
  });

  it("only fills its parent when asked", () => {
    const { container, rerender } = render(<OrbLoader />);
    expect(container.firstElementChild).not.toHaveClass("h-full");
    expect(container.firstElementChild?.className).not.toMatch(/vh/);
    rerender(<OrbLoader fill />);
    expect(container.firstElementChild).toHaveClass("h-full");
  });

  it("passes the app's resolved theme, falling back to auto", () => {
    resolvedTheme = "light";
    const { rerender } = render(<OrbLoader />);
    expect(screen.getByTestId("orb")).toHaveAttribute("data-theme", "light");
    resolvedTheme = undefined;
    rerender(<OrbLoader label="again" />);
    expect(screen.getByTestId("orb")).toHaveAttribute("data-theme", "auto");
    resolvedTheme = "dark";
  });
});

describe("OwlLoader (deprecated alias)", () => {
  it("renders the region orb and keeps the old frame height", () => {
    const { container } = render(<OwlLoader src="/owl.json" size={80} label="Crunching" />);
    expect(screen.getByRole("status", { name: "Crunching" })).toBeInTheDocument();
    expect(container.firstElementChild).toHaveClass("min-h-[50vh]");
    expect(screen.getByTestId("orb")).toHaveAttribute("data-size", "64");
  });
});

describe("WorkingOrb", () => {
  it("is a decorative 20px orb that accepts a state", () => {
    const { container } = render(<WorkingOrb state="searching" />);
    const host = container.querySelector('[data-working-orb="true"]');
    expect(host).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByTestId("orb")).toHaveAttribute("data-size", "20");
    expect(screen.getByTestId("orb")).toHaveAttribute("data-state", "searching");
  });
});
