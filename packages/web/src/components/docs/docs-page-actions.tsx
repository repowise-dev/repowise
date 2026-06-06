"use client";

import { useState } from "react";
import {
  ChevronDown,
  Download,
  ExternalLink,
  FileText,
  FolderArchive,
  Loader2,
  PanelRight,
  PanelRightClose,
} from "lucide-react";
import { Button } from "@repowise-dev/ui/ui/button";
import { ConfidenceBadge } from "@repowise-dev/ui/wiki/confidence-badge";
import {
  READER_PERSONAS,
  type ReaderPersona,
} from "@repowise-dev/ui/docs/reader-persona";
import { cn } from "@/lib/utils/cn";
import { downloadTextFile } from "@/lib/utils/download";
import type { PageResponse } from "@/lib/api/types";

/**
 * Per-page controls for the DocsHeader row — the compact freshness badge and
 * the reader-level control that used to live in a second sticky row above the
 * article. The reader control renders only when filtering actually changes
 * this page (see personaFilteringApplies); on curated pages it disappears.
 */
export function DocsPageActions({
  page,
  persona,
  setPersona,
  personaHasEffect,
}: {
  page: PageResponse;
  persona: ReaderPersona;
  setPersona: (next: ReaderPersona) => void;
  personaHasEffect: boolean;
}) {
  return (
    <>
      <ConfidenceBadge score={page.confidence} status={page.freshness_status} />
      {personaHasEffect && (
        <div
          className="hidden md:inline-flex items-center rounded-md border border-[var(--color-border-default)] p-0.5 shrink-0"
          role="group"
          aria-label="Reader level"
        >
          {READER_PERSONAS.map((p) => (
            <button
              key={p.value}
              onClick={() => setPersona(p.value)}
              title={p.hint}
              className={cn(
                "rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors",
                persona === p.value
                  ? "bg-[var(--color-accent-primary)] text-[var(--color-text-inverse)]"
                  : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]",
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
      )}
    </>
  );
}

/** Insights-drawer toggle — header-resident; the drawer itself is lg-only. */
export function SidebarToggle({
  open,
  onToggle,
}: {
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={onToggle}
      className={cn(
        "hidden lg:inline-flex rounded-md p-1.5 transition-colors",
        open
          ? "text-[var(--color-accent)] hover:bg-[var(--color-bg-elevated)]"
          : "text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-accent-primary)]",
      )}
      title={open ? "Hide insights" : "Show insights"}
      aria-pressed={open}
    >
      {open ? (
        <PanelRightClose className="h-3.5 w-3.5" />
      ) : (
        <PanelRight className="h-3.5 w-3.5" />
      )}
    </button>
  );
}

/**
 * Single Export menu — the former standalone download/open-external icon
 * buttons fold in here as per-page items, above the repo-wide exports.
 */
export function ExportMenu({
  isExporting,
  onExportAll,
  zipHref,
  page,
  repoId,
}: {
  isExporting: boolean;
  onExportAll: () => void;
  zipHref: string;
  page: PageResponse | null;
  repoId: string;
}) {
  const [open, setOpen] = useState(false);
  const itemClass =
    "flex w-full items-center gap-2 rounded px-2 py-1.5 text-xs text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]";

  const exportPage = () => {
    if (!page) return;
    const filename = (page.target_path || page.title).replace(/\//g, "_") + ".md";
    const header = `# ${page.title}\n\n> Path: ${page.target_path}\n\n`;
    downloadTextFile(header + page.content, filename);
  };

  return (
    <div className="relative">
      <Button
        variant="outline"
        size="sm"
        onClick={() => setOpen((o) => !o)}
        disabled={isExporting}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        {isExporting ? (
          <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
        ) : (
          <Download className="h-3.5 w-3.5 mr-1.5" />
        )}
        Export
        <ChevronDown className="h-3 w-3 ml-1 opacity-60" />
      </Button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div
            role="menu"
            className="absolute right-0 top-full z-20 mt-1 w-52 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-1 shadow-md"
          >
            {page && (
              <>
                <button
                  role="menuitem"
                  className={itemClass}
                  onClick={() => {
                    setOpen(false);
                    exportPage();
                  }}
                >
                  <Download className="h-3.5 w-3.5 shrink-0" />
                  This page (Markdown)
                </button>
                <a
                  role="menuitem"
                  href={`/repos/${repoId}/wiki/${encodeURIComponent(page.id)}`}
                  className={itemClass}
                  onClick={() => setOpen(false)}
                >
                  <ExternalLink className="h-3.5 w-3.5 shrink-0" />
                  Open full page
                </a>
                <div className="my-1 border-t border-[var(--color-border-default)]" />
              </>
            )}
            <button
              role="menuitem"
              className={itemClass}
              onClick={() => {
                setOpen(false);
                onExportAll();
              }}
            >
              <FileText className="h-3.5 w-3.5 shrink-0" />
              All pages (Markdown)
            </button>
            <a
              role="menuitem"
              href={zipHref}
              download
              className={itemClass}
              onClick={() => setOpen(false)}
            >
              <FolderArchive className="h-3.5 w-3.5 shrink-0" />
              ZIP archive
            </a>
          </div>
        </>
      )}
    </div>
  );
}
