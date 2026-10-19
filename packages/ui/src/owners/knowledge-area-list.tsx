"use client";

import * as React from "react";
import { formatScore } from "@repowise-dev/types/health";
import { RowPromptAction } from "../health/row-prompt-action";
import { scoreTextColor } from "../health/tokens";
import { fileEntityPath } from "../shared/entity";
import { PathText } from "../shared/path-text";
import type { OwnershipLabels } from "./labels";

/**
 * Directories of files that carry knowledge risk, area first and files on
 * demand: the shape the Knowledge risks, Knowledge loss and Off-boarding views
 * and the person page all share. Each area carries a quiet prompt for its
 * files, shown on hover and focus; the one filled prompt sits on the section.
 */

export interface KnowledgeAreaFile {
  path: string;
  nloc: number;
  healthScore: number | null;
  isHotspot: boolean;
  lowHealth: boolean;
  /** Right-aligned detail, e.g. "ana 92%" or "loses 60%". */
  note?: string;
}

export interface KnowledgeAreaRowProps {
  dir: string;
  files: KnowledgeAreaFile[];
  /** One line under the path: who holds it, who could take over. */
  summary?: React.ReactNode;
  onPrompt: () => void;
  /** `/repos/{id}`: file rows link to the file page under it. */
  base?: string | undefined;
  LinkComponent?: React.ElementType | undefined;
  defaultOpen?: boolean;
  labels: OwnershipLabels;
}

/** Files listed before "Show all N". */
const FILE_LIMIT = 12;

function FileMarks({ file, hotspot }: { file: KnowledgeAreaFile; hotspot: string }) {
  return (
    <span className="flex shrink-0 items-baseline gap-2 font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-tertiary)]">
      {file.isHotspot && <span>{hotspot}</span>}
      {file.lowHealth && file.healthScore != null && (
        <span className={`normal-case tracking-normal ${scoreTextColor(file.healthScore)}`}>
          {formatScore(file.healthScore)}
        </span>
      )}
    </span>
  );
}

export function KnowledgeAreaRow({
  dir,
  files,
  summary,
  onPrompt,
  base,
  LinkComponent,
  defaultOpen = false,
  labels,
}: KnowledgeAreaRowProps) {
  const [open, setOpen] = React.useState(defaultOpen);
  const panelId = React.useId();

  return (
    <li className="group/row border-b border-[var(--color-border-default)] py-3">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((o) => !o)}
          className="group flex min-w-0 flex-1 items-start gap-2 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
        >
          <span
            aria-hidden
            className={`mt-0.5 text-[var(--color-text-tertiary)] transition-transform ${open ? "rotate-90" : ""}`}
          >
            ▸
          </span>
          <span className="min-w-0">
            <span className="flex flex-wrap items-baseline gap-x-2">
              <PathText path={dir ? `${dir}/` : labels.repositoryRoot} className="text-[13px]" />
              <span className="font-mono text-xs tabular-nums text-[var(--color-text-tertiary)]">
                {labels.files(files.length)}
              </span>
            </span>
            {summary && (
              <span className="mt-0.5 block text-xs leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
                {summary}
              </span>
            )}
          </span>
        </button>
        <RowPromptAction
          onClick={onPrompt}
          label={labels.promptForGroup(files.length)}
          subject={dir || labels.repositoryRoot}
        />
      </div>

      {open && (
        <AreaFiles id={panelId} dir={dir} files={files} base={base} LinkComponent={LinkComponent} labels={labels} />
      )}
    </li>
  );
}

/** An open area's files, named relative to the area, the first few then "Show all". */
function AreaFiles({
  id,
  dir,
  files,
  base,
  LinkComponent,
  labels,
}: {
  id: string;
  dir: string;
  files: KnowledgeAreaFile[];
  base?: string | undefined;
  LinkComponent?: React.ElementType | undefined;
  labels: OwnershipLabels;
}) {
  const [all, setAll] = React.useState(false);
  const shown = all ? files : files.slice(0, FILE_LIMIT);
  return (
    <ul id={id} className="mt-2 flex flex-col pl-5">
      {shown.map((f) => (
        <AreaFileRow key={f.path} file={f} dir={dir} base={base} LinkComponent={LinkComponent} hotspot={labels.hotspot} />
      ))}
      {files.length > shown.length && (
        <li>
          <button
            type="button"
            onClick={() => setAll(true)}
            className="mt-1 text-xs text-[var(--color-accent-primary)] hover:underline"
          >
            {labels.showAllFiles(files.length)}
          </button>
        </li>
      )}
    </ul>
  );
}

function AreaFileRow({
  file,
  dir,
  base,
  LinkComponent,
  hotspot,
}: {
  file: KnowledgeAreaFile;
  dir: string;
  base?: string | undefined;
  LinkComponent?: React.ElementType | undefined;
  hotspot: string;
}) {
  const A = LinkComponent ?? "a";
  const name = <PathText path={dir ? file.path.slice(dir.length + 1) : file.path} />;
  return (
    <li className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 py-1">
      <span className="flex min-w-0 items-baseline gap-2">
        {base ? (
          <A href={fileEntityPath(base, file.path)} className="group min-w-0 no-underline" title={file.path}>
            {name}
          </A>
        ) : (
          name
        )}
        <FileMarks file={file} hotspot={hotspot} />
      </span>
      {file.note && (
        <span className="font-mono text-xs tabular-nums text-[var(--color-text-tertiary)]">{file.note}</span>
      )}
    </li>
  );
}

/** Areas in order, the first `initial` shown, the rest behind one button. */
export function KnowledgeAreaList<T>({
  areas,
  keyOf,
  renderArea,
  initial = 8,
  label,
  showAllLabel,
}: {
  areas: readonly T[];
  keyOf: (area: T) => string;
  renderArea: (area: T, index: number) => React.ReactNode;
  initial?: number;
  /** Accessible name of the list, e.g. "Areas at risk". */
  label: string;
  /** "Show all N directories". */
  showAllLabel: (count: number) => string;
}) {
  const [limit, setLimit] = React.useState(initial);
  const shown = areas.slice(0, limit);
  return (
    <div className="flex flex-col gap-2">
      <ul aria-label={label} className="flex flex-col border-t border-[var(--color-border-default)]">
        {shown.map((a, i) => (
          <React.Fragment key={keyOf(a)}>{renderArea(a, i)}</React.Fragment>
        ))}
      </ul>
      {areas.length > shown.length && (
        <button
          type="button"
          onClick={() => setLimit(areas.length)}
          className="self-start text-xs text-[var(--color-accent-primary)] hover:underline"
        >
          {showAllLabel(areas.length)}
        </button>
      )}
    </div>
  );
}
