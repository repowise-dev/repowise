"use client";

import * as React from "react";

import { CodeBlock } from "./plan-detail";

/** Lines of a span shown inline before the excerpt says it was cut. */
const EXCERPT_MAX_LINES = 40;

/**
 * A span of a file, read the way the file view reads it and cut to the lines
 * the analysis stored. Says so when the checkout no longer has those lines.
 */
export function SourceExcerpt({
  path,
  start,
  end,
  readSource,
  widen,
}: {
  path: string;
  start: number;
  end: number;
  readSource: (path: string) => Promise<string>;
  /**
   * Move the start up once the file is read, e.g. to the loop header above a
   * call. Returns the new first line and a note to print under the block.
   */
  widen?: ((fileLines: string[]) => { start: number; note: string | null }) | undefined;
}) {
  const [state, setState] = React.useState<
    { status: "loading" } | { status: "done"; text: string } | { status: "error" }
  >({ status: "loading" });
  React.useEffect(() => {
    let live = true;
    readSource(path).then(
      (text) => live && setState({ status: "done", text }),
      () => live && setState({ status: "error" }),
    );
    return () => {
      live = false;
    };
  }, [path, readSource]);

  if (state.status === "loading") {
    return <p className="text-[11.5px] text-[var(--color-text-tertiary)]">Reading the file.</p>;
  }
  if (state.status === "error") {
    return (
      <p className="text-[11.5px] text-[var(--color-text-tertiary)]">
        The file could not be read from the checkout.
      </p>
    );
  }
  const fileLines = state.text.split("\n");
  const widened = widen ? widen(fileLines) : null;
  const first = widened ? Math.min(widened.start, start) : start;
  const lines = fileLines.slice(first - 1, end);
  const shown = lines.slice(0, EXCERPT_MAX_LINES);
  if (shown.length === 0) {
    return (
      <p className="text-[11.5px] text-[var(--color-text-tertiary)]">
        Lines {start} to {end} are past the end of the file in the checkout; it changed since the
        analysis ran.
      </p>
    );
  }
  return (
    <div className="space-y-1">
      <CodeBlock code={shown.join("\n")} startLine={first} />
      {lines.length > shown.length ? (
        <p className="text-[11.5px] text-[var(--color-text-tertiary)]">
          First {shown.length} of {lines.length} lines shown.
        </p>
      ) : null}
      {widened?.note ? (
        <p className="text-[11.5px] text-[var(--color-text-tertiary)]">{widened.note}</p>
      ) : null}
    </div>
  );
}
