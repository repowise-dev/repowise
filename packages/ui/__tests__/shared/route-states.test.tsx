import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { ApiError } from "../../src/shared/api-error";
import { RouteError, RouteNotFound } from "../../src/shared/route-states";

describe("RouteError", () => {
  it("shows the plain sentence, the digest in mono and a retry", () => {
    const reset = vi.fn();
    render(
      <RouteError message="This page could not be loaded." digest="abc123" onRetry={reset} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("This page could not be loaded.");
    expect(screen.getByText(/abc123/)).toHaveClass("font-mono");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledOnce();
  });

  it("styles a host link as the way back", () => {
    render(<RouteError back={<a href="/">Back to dashboard</a>} />);
    expect(screen.getByRole("link", { name: "Back to dashboard" })).toHaveAttribute("href", "/");
  });
});

describe("RouteNotFound", () => {
  it("is neutral, not an alert", () => {
    render(<RouteNotFound title="Repository not found" links={[<a key="d" href="/">Back</a>]} />);
    expect(
      screen.getByRole("heading", { level: 1, name: "Repository not found" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back" })).toBeInTheDocument();
    expect(screen.getByText("404")).toBeInTheDocument();
  });
});

describe("ApiError", () => {
  it("compact renders one line with retry", () => {
    const retry = vi.fn();
    const { container } = render(
      <ApiError size="compact" title="Failed to load" message="Server down." onRetry={retry} />,
    );
    expect(container.firstElementChild).not.toHaveClass("py-12");
    expect(screen.getByRole("alert")).toHaveTextContent("Server down.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledOnce();
  });
});
