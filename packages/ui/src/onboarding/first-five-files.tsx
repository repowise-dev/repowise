"use client";

import { Sparkles, ArrowUpRight } from "lucide-react";
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
}

export function FirstFiveFiles({
  files,
  hrefFor,
  title = "First five files",
}: FirstFiveFilesProps) {
  if (files.length === 0) return null;
  const top = files.slice(0, 5);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-[var(--color-accent-primary)]" />
          {title}
          <span className="text-[10px] font-normal text-[var(--color-text-tertiary)] uppercase tracking-wider">
            start here
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <ol className="space-y-1.5">
          {top.map((f, i) => {
            const href = hrefFor?.(f) ?? f.doc_url;
            const Wrapper = (props: { children: React.ReactNode }) =>
              href ? (
                <a
                  href={href}
                  className="group flex items-start gap-2.5 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-3 py-2 hover:border-[var(--color-accent-primary)] transition-colors"
                >
                  {props.children}
                </a>
              ) : (
                <div className="flex items-start gap-2.5 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-3 py-2">
                  {props.children}
                </div>
              );
            return (
              <li key={f.file_path}>
                <Wrapper>
                  <span className="text-xs tabular-nums text-[var(--color-text-tertiary)] mt-0.5">{i + 1}</span>
                  <div className="min-w-0 flex-1">
                    <p className="font-mono text-xs text-[var(--color-text-primary)] truncate">
                      {f.file_path}
                    </p>
                    <div className="flex flex-wrap items-center gap-1.5 mt-0.5">
                      {f.is_entry_point && <Badge variant="accent" className="h-4 text-[10px]">entry point</Badge>}
                      {f.has_doc && <Badge variant="outline" className="h-4 text-[10px]">has doc</Badge>}
                      {f.reason && (
                        <span className="text-[11px] text-[var(--color-text-secondary)]">{f.reason}</span>
                      )}
                    </div>
                  </div>
                  {href && (
                    <ArrowUpRight className="h-3.5 w-3.5 text-[var(--color-text-tertiary)] mt-0.5 opacity-0 group-hover:opacity-100 transition-opacity" />
                  )}
                </Wrapper>
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}
