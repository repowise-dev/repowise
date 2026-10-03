import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import type { RelatedWorkFile, RelatedWorkItem } from "@repowise-dev/types/health";
import { RelatedWork, useRelatedWork } from "../../src/health/related-work.js";
import { HealthFileDrawer } from "../../src/health/health-file-drawer.js";

const FILE: RelatedWorkFile = {
  file_path: "src/repo.py",
  lenses: {
    findings: {
      items: [
        { lens: "findings", id: "f1", kind: "complex_method", severity: "high", symbol: "load", line: 12 },
      ],
      total: 1,
    },
    refactoring: {
      items: [{ lens: "refactoring", id: "refop_1", kind: "extract_method", tier: "M", rank: 1 }],
      total: 1,
    },
    performance: {
      items: [
        { lens: "performance", id: "perf_1", kind: "io_in_loop", symbol: "src/repo.py::load_all", tier: "advisory" },
      ],
      total: 7,
    },
    dead_code: {
      items: [{ lens: "dead_code", id: "d1", kind: "unused_export", symbol: "old_helper", tier: "review", line: 40 }],
      total: 1,
    },
  },
};

const href = (item: RelatedWorkItem) => `/go/${item.lens}/${item.id}`;

describe("RelatedWork", () => {
  it("renders nothing without data or with nothing outside its own lens", () => {
    const { container, rerender } = render(<RelatedWork related={undefined} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<RelatedWork related={{ file_path: "a.py", lenses: {} }} />);
    expect(container).toBeEmptyDOMElement();
    rerender(
      <RelatedWork
        related={{ file_path: "a.py", lenses: { findings: FILE.lenses!.findings! } }}
        exclude={["findings"]}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("leaves out the lens it sits in and links the rest", () => {
    render(<RelatedWork related={FILE} exclude={["refactoring"]} relatedWorkHref={href} />);
    expect(screen.queryByText("Refactoring")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Unused export: old_helper/ })).toHaveAttribute(
      "href",
      "/go/dead_code/d1",
    );
    expect(screen.getByRole("link", { name: /load_all/ })).toHaveAttribute(
      "href",
      "/go/performance/perf_1",
    );
    // A capped lens says how much it is showing.
    expect(screen.getByText("1 of 7")).toBeInTheDocument();
  });

  it("is plain text without an href and routes through the host when asked", () => {
    const { rerender } = render(<RelatedWork related={FILE} />);
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    const onNavigate = vi.fn();
    rerender(<RelatedWork related={FILE} relatedWorkHref={href} onNavigate={onNavigate} />);
    fireEvent.click(screen.getByRole("link", { name: /Extract method/i }));
    expect(onNavigate).toHaveBeenCalledWith("/go/refactoring/refop_1");
  });
});

describe("HealthFileDrawer related work", () => {
  it("lists the other lenses but never repeats the drawer's own findings", () => {
    const onNavigate = vi.fn();
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={{
          file_path: "src/repo.py",
          score: 5,
          max_ccn: 10,
          max_nesting: 3,
          nloc: 100,
          module: "src",
          has_test_file: false,
        }}
        related={FILE}
        relatedWorkHref={href}
        onNavigate={onNavigate}
      />,
    );
    expect(screen.getByText("Elsewhere for this file")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: /Unused export/ }));
    expect(onNavigate).toHaveBeenCalledWith("/go/dead_code/d1");
    expect(screen.getByRole("link", { name: /Extract method/i })).toHaveAttribute(
      "href",
      "/go/refactoring/refop_1",
    );
    expect(screen.queryByRole("link", { name: /\/go\/findings/ })).not.toBeInTheDocument();
    expect(screen.queryAllByRole("link").map((a) => a.getAttribute("href"))).not.toContain(
      "/go/findings/f1",
    );
  });
});

describe("useRelatedWork", () => {
  it("asks once per set of paths and not at all without a fetcher or a path", async () => {
    const fetcher = vi.fn(async (paths: string[]) => ({
      files: paths.map((file_path) => ({ file_path, lenses: {} })),
      per_lens_limit: 5,
    }));
    const { result, rerender } = renderHook(
      ({ paths }: { paths: (string | null)[] }) => useRelatedWork(fetcher, paths),
      { initialProps: { paths: ["a.py"] as (string | null)[] } },
    );
    await waitFor(() => expect(result.current?.files?.[0]?.file_path).toBe("a.py"));
    rerender({ paths: ["a.py"] });
    expect(fetcher).toHaveBeenCalledTimes(1);
    rerender({ paths: [null] });
    expect(result.current).toBeUndefined();
    expect(fetcher).toHaveBeenCalledTimes(1);
    const none = renderHook(() => useRelatedWork(undefined, ["a.py"]));
    expect(none.result.current).toBeUndefined();
  });
});
