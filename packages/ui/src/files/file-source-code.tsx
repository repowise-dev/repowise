"use client";

import { useEffect, useRef } from "react";

export interface FileSourceCodeProps {
  /**
   * Shiki-highlighted source HTML where each `.line` carries `data-line="N"`
   * (real file line numbers) and, on the Coverage tab, `data-covered="y" | "n"`.
   * The host adds both through a shiki transformer.
   */
  html: string;
  /** Names the file a selection inside the source came from. */
  filePath?: string | undefined;
  /**
   * A 1-based line to scroll to and mark, from a `?line=N` deep link. A value
   * that is not a positive integer, or names a line the HTML does not have,
   * is ignored.
   */
  highlightLine?: number | undefined;
}

/**
 * The file's highlighted source, shared by the Overview deep-link view and the
 * Coverage tab. A client component only for the one effect that scrolls the
 * linked line into view; the markup itself is the host's shiki output.
 */
export function FileSourceCode({ html, filePath, highlightLine }: FileSourceCodeProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (highlightLine === undefined || !Number.isInteger(highlightLine) || highlightLine < 1) return;
    const row = ref.current?.querySelector<HTMLElement>(`.line[data-line="${highlightLine}"]`);
    if (!row) return;
    row.setAttribute("data-highlighted", "");
    row.setAttribute("aria-current", "location");
    row.scrollIntoView({ block: "center" });
    return () => {
      row.removeAttribute("data-highlighted");
      row.removeAttribute("aria-current");
    };
  }, [html, highlightLine]);

  return (
    <div
      ref={ref}
      data-chat-selection=""
      {...(filePath ? { "data-chat-selection-path": filePath } : {})}
      className="file-source-code overflow-x-auto border-y border-[var(--color-border-default)] text-xs leading-relaxed"
    >
      <div dangerouslySetInnerHTML={{ __html: html /* shiki output; repowise-security-ignore: unsafe_inner_html */ }} />
      {/* A linked line takes the neutral selection wash, not a status colour,
          so it never reads as covered or uncovered; on the Coverage tab the
          coverage edge stays. */}
      <style>{`
        .file-source-code pre { margin: 0; padding: 0.75rem 0; background: transparent !important; }
        .file-source-code code { display: block; }
        .file-source-code .line { display: inline-block; width: 100%; padding: 0 0.75rem 0 0.5rem; border-left: 3px solid transparent; }
        .file-source-code .line[data-covered="y"] { border-left-color: var(--color-success); background: color-mix(in srgb, var(--color-success) 7%, transparent); }
        .file-source-code .line[data-covered="n"] { border-left-color: var(--color-error); background: color-mix(in srgb, var(--color-error) 8%, transparent); }
        .file-source-code .line[data-highlighted] { background: var(--color-selection-wash); }
        .file-source-code .line[data-highlighted]:not([data-covered]) { border-left-color: var(--color-selection-border); }
      `}</style>
    </div>
  );
}
