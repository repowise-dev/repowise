import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { fileHref, parseFileHref, useFileLinks } from "./fileLinks";
import type { WebviewHost } from "./rpc";

afterEach(cleanup);

function Links({ host }: { host: WebviewHost }) {
  useFileLinks(host);
  return (
    <>
      <a href={fileHref("src/a b.ts", 12)}>with line</a>
      <a href={fileHref("src/c.ts")}>no line</a>
    </>
  );
}

describe("file links", () => {
  it("round-trips a path and line", () => {
    expect(parseFileHref(fileHref("src/a b.ts", 12))).toEqual({ path: "src/a b.ts", line: 12 });
    expect(parseFileHref(fileHref("src/c.ts"))).toEqual({ path: "src/c.ts" });
    expect(parseFileHref("/repos/r1/files/src/c.ts")).toBeNull();
  });

  it("opens the file in the editor instead of navigating", () => {
    const openFile = vi.fn();
    render(<Links host={{ openFile } as unknown as WebviewHost} />);
    fireEvent.click(screen.getByText("with line"));
    fireEvent.click(screen.getByText("no line"));
    expect(openFile).toHaveBeenNthCalledWith(1, "src/a b.ts", 12);
    expect(openFile).toHaveBeenNthCalledWith(2, "src/c.ts", undefined);
  });
});
