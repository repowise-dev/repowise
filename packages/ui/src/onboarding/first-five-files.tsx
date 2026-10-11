"use client";

import { useState } from "react";
import { Sparkles, ArrowUpRight, ChevronDown, ChevronRight } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Badge } from "../ui/badge";

export interface FirstFiveFile {
  file_path: string;
  /** Why this file is a good starting point — entry point, central, etc. */
  reason?: string;
  pagerank?: number;
  is_entry_point?: boolean;
  has_doc?: boolean;
  doc_url?: string;
}

export interface FirstFiveFilesProps {
  files: FirstFiveFile[];
  /** Per-file deep link factory (e.g. wiki page). Falls back to `doc_url` field. */
  hrefFor?: (file: FirstFiveFile) => string | undefined;
  /** Override the title (e.g. for a "Start here" pinned section). */
  title?: string;
  /** Render the header as a toggle that collapses the file list. */
  collapsible?: boolean;
  /** When collapsible, start collapsed. Defaults to false. */
  defaultCollapsed?: boolean;
}

export function FirstFiveFiles({
  files,
  hrefFor,
  title = "First five files",
  collapsible = false,
  defaultCollapsed = false,
}: FirstFiveFilesProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  if (files.length === 0) return null;
  const top = files.slice(0, 5);

  const header = (
    <CardTitle className="text-[13px] flex items-center gap-1.5">
      {collapsible &&
        (collapsed ? (
          <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
        ))}
      <Sparkles className="h-3.5 w-3.5 text-[var(--color-accent-primary)]" />
      {title}
      <span className="text-[10px] font-normal text-[var(--color-text-tertiary)] uppercase tracking-wider">
        the first {top.length} files to read
      </span>
    </CardTitle>
  );

  return (
    <Card>
      <CardHeader className="p-2.5">
        {collapsible ? (
          <button
            type="button"
            onClick={() => setCollapsed((c) => !c)}
            aria-expanded={!collapsed}
            className="flex w-full items-center text-left"
          >
            {header}
          </button>
        ) : (
          header
        )}
      </CardHeader>
      {!collapsed && (
      <CardContent className="p-2.5 pt-0">
        <ol className="space-y-1">
          {top.map((f, i) => {
            const href = hrefFor?.(f) ?? f.doc_url;
            const Wrapper = (props: { children: React.ReactNode }) =>
              href ? (
                <a
                  href={href}
                  className="group flex items-center gap-2 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2.5 py-1.5 hover:border-[var(--color-accent-primary)] transition-colors"
                >
                  {props.children}
                </a>
              ) : (
                <div className="flex items-center gap-2 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2.5 py-1.5">
                  {props.children}
                </div>
              );
            return (
              <li key={f.file_path}>
                <Wrapper>
                  <span className="text-xs tabular-nums text-[var(--color-text-tertiary)]">{i + 1}</span>
                  <p className="font-mono text-xs text-[var(--color-text-primary)] truncate min-w-0 flex-1">
                    {f.file_path}
                  </p>
                  {f.is_entry_point && <Badge variant="accent" className="h-4 text-[10px] shrink-0">entry</Badge>}
                  {f.has_doc && <Badge variant="outline" className="h-4 text-[10px] shrink-0">doc</Badge>}
                  {href && (
                    <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)] opacity-0 group-hover:opacity-100 transition-opacity" />
                  )}
                </Wrapper>
              </li>
            );
          })}
        </ol>
      </CardContent>
      )}
    </Card>
  );
}
