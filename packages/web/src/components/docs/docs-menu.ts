import { Download, FileText, FolderArchive, Presentation, Sparkles } from "lucide-react";
import type { DocsMenuGroup } from "@repowise-dev/ui/docs/docs-more-menu";
import { READER_PERSONAS, type ReaderPersona } from "@repowise-dev/ui/docs/reader-persona";

type Translate = (key: string) => string;

export interface DocsMenuInput {
  t: Translate;
  /** A page is open, so "Export this page" applies. */
  hasPage: boolean;
  onExportPage: () => void;
  onExportAll: () => void;
  /** An export is running; its items wait until it finishes. */
  exporting: boolean;
  zipHref: string;
  /** Present opens only when the deck has pages to draw on. */
  onPresent: (() => void) | null;
  /** The bulk write, offered only while a subsystem page is still unwritten. */
  writeAll: { open: () => void; running: boolean } | null;
  /** The reader-level choice, for a phone where the bar hides its control. */
  reader: { persona: ReaderPersona; setPersona: (next: ReaderPersona) => void } | null;
}

/**
 * The docs bar's More menu: export, present and the one bulk model action,
 * grouped by kind. The reader-level group is phone-only and last, since the
 * bar shows that control itself from `sm` up.
 */
export function buildDocsMenuGroups(input: DocsMenuInput): DocsMenuGroup[] {
  const { t, writeAll, onPresent, reader } = input;
  return [
    {
      key: "export",
      items: [
        ...(input.hasPage
          ? [{ key: "export-page", label: t("explorer.menu.exportPage"), icon: Download, onSelect: input.onExportPage, disabled: input.exporting }]
          : []),
        { key: "export-all", label: t("explorer.menu.exportAll"), icon: FileText, onSelect: input.onExportAll, disabled: input.exporting },
        { key: "export-zip", label: t("explorer.menu.exportZip"), icon: FolderArchive, href: input.zipHref, download: true },
      ],
    },
    {
      key: "present",
      items: onPresent
        ? [{ key: "present", label: t("explorer.menu.present"), icon: Presentation, onSelect: onPresent }]
        : [],
    },
    {
      key: "model",
      items: writeAll
        ? [{
            key: "write-all",
            label: t("bulk.button"),
            icon: Sparkles,
            tone: "model" as const,
            onSelect: writeAll.open,
            disabled: writeAll.running,
          }]
        : [],
    },
    {
      key: "reader",
      label: t("explorer.menu.readerLevel"),
      phoneOnly: true,
      items: reader
        ? READER_PERSONAS.map((p) => ({
            key: `reader-${p.value}`,
            label: p.label,
            checked: reader.persona === p.value,
            onSelect: () => reader.setPersona(p.value),
          }))
        : [],
    },
  ];
}
