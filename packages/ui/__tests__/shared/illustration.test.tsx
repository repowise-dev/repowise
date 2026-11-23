import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { renderToString } from "react-dom/server";
import { Inbox } from "lucide-react";

import { CleanState } from "../../src/shared/clean-state";
import { EmptyState } from "../../src/shared/empty-state";
import { IllustrationProvider, type Illustrations } from "../../src/shared/illustration";
import { RouteError, RouteNotFound } from "../../src/shared/route-states";

const art = (kind: string) => vi.fn(({ size }: { size: number }) => (
  <svg data-testid={`art-${kind}`} width={size} />
));

function withArt(ui: ReactElement, illustrations: Illustrations) {
  return render(<IllustrationProvider illustrations={illustrations}>{ui}</IllustrationProvider>);
}

describe("illustration slot", () => {
  it("renders exactly the built-in markup when no provider is mounted", () => {
    const plain = render(<EmptyState icon={<Inbox />} title="No commits yet" />).container.innerHTML;
    const wrapped = withArt(<EmptyState icon={<Inbox />} title="No commits yet" />, {}).container
      .innerHTML;
    expect(wrapped).toBe(plain);
    expect(render(<RouteNotFound />).container.innerHTML).toBe(
      withArt(<RouteNotFound />, {}).container.innerHTML,
    );
  });

  it("leaves every tone and RouteError unchanged with an empty provider", () => {
    const cases: ReactElement[] = [
      <EmptyState key="n" title="No commits yet" />,
      <EmptyState key="p" tone="positive" title="No dead code" />,
      <EmptyState key="e" tone="error" icon={<Inbox />} title="Could not load" />,
      <EmptyState key="e2" tone="error" title="Could not load" />,
      <EmptyState key="f" tone="filtered" title="No matches" />,
      <RouteError key="r" digest="abc" />,
    ];
    for (const ui of cases) {
      const plain = render(ui).container.innerHTML;
      expect(withArt(ui, {}).container.innerHTML).toBe(plain);
    }
  });

  it("server-renders with no provider", () => {
    const html = renderToString(<EmptyState tone="positive" title="No security findings" />);
    expect(html).toContain("No security findings");
  });

  it("draws the host's art per kind, with the title and size", () => {
    const empty = art("empty");
    withArt(<EmptyState title="No commits yet" />, { empty });
    expect(screen.getByTestId("art-empty")).toBeInTheDocument();
    expect(empty).toHaveBeenCalledWith({ title: "No commits yet", size: 96 });
  });

  it("uses clean for positive states and CleanState, replacing the check", () => {
    const { container } = withArt(<CleanState title="No dead code across 12 files" />, {
      clean: art("clean"),
      empty: art("empty"),
    });
    expect(screen.getByTestId("art-clean")).toBeInTheDocument();
    expect(screen.queryByTestId("art-empty")).not.toBeInTheDocument();
    expect(container.querySelector(".bg-\\[var\\(--color-success-muted\\)\\]")).toBeNull();
  });

  it("keeps an explicit icon over the host art", () => {
    withArt(<EmptyState icon={<Inbox data-testid="icon" />} title="No commits yet" />, {
      empty: art("empty"),
    });
    expect(screen.getByTestId("icon")).toBeInTheDocument();
    expect(screen.queryByTestId("art-empty")).not.toBeInTheDocument();
  });

  it("lets clean art win over a call-site icon", () => {
    withArt(
      <EmptyState tone="positive" icon={<Inbox data-testid="icon" />} title="No couplings detected" />,
      { clean: art("clean") },
    );
    expect(screen.getByTestId("art-clean")).toBeInTheDocument();
    expect(screen.queryByTestId("icon")).not.toBeInTheDocument();
  });

  it("ignores the host art on compact and filtered states", () => {
    const empty = art("empty");
    const clean = art("clean");
    withArt(
      <>
        <EmptyState size="compact" title="No snapshots yet" />
        <EmptyState size="compact" tone="positive" title="No direct risks" />
        <EmptyState tone="filtered" title="No matches" />
      </>,
      { empty, clean },
    );
    expect(empty).not.toHaveBeenCalled();
    expect(clean).not.toHaveBeenCalled();
  });

  it("gives error tones error art only, and filtered tones none", () => {
    const empty = art("empty");
    const clean = art("clean");
    const error = art("error");
    withArt(
      <>
        <EmptyState tone="error" title="Could not load" />
        <EmptyState tone="filtered" size="default" title="No matches" />
      </>,
      { empty, clean, error },
    );
    expect(error).toHaveBeenCalledOnce();
    expect(error).toHaveBeenCalledWith({ title: "Could not load", size: 96 });
    expect(empty).not.toHaveBeenCalled();
    expect(clean).not.toHaveBeenCalled();
  });

  it("draws not-found and error art on route states unless an icon is passed", () => {
    const notFound = art("notFound");
    const error = art("error");
    withArt(
      <>
        <RouteNotFound title="Repository not found" />
        <RouteError title="Something went wrong" />
      </>,
      { notFound, error },
    );
    expect(notFound).toHaveBeenCalledWith({ title: "Repository not found", size: 128 });
    expect(error).toHaveBeenCalledWith({ title: "Something went wrong", size: 128 });

    withArt(<RouteError icon={<Inbox data-testid="route-icon" />} />, { error: art("error2") });
    expect(screen.getByTestId("route-icon")).toBeInTheDocument();
    expect(screen.queryByTestId("art-error2")).not.toBeInTheDocument();
  });
});
