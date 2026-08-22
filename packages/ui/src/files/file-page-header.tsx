import * as React from "react";
import { bandForScore, HEALTH_BAND_LABEL } from "@repowise-dev/types/health";
import type { FileDetailResponse } from "@repowise-dev/types/files";
import { PageLede } from "../shared/page-lede";
import { StatRibbon, type RibbonStat } from "../stats/stat-ribbon";
import { healthBandInk, coverageTextColor } from "../health/tokens";
import { formatLOC, formatNumber } from "../lib/format";
import { FileMarks } from "./file-marks";

export interface FilePageHeaderProps {
  data: FileDetailResponse;
  /** Route prefix for the repo, e.g. `/repos/:id`. */
  linkPrefix: string;
  /**
   * Deep link into the docs reading surface for this file's page.
   *
   * It lives in the header rather than in the Doc tab, where it used to be the
   * only way to reach the wiki from here: a reader on Health or Dependencies
   * had no idea a documentation page existed, and the one link to it was
   * behind the tab that already shows the page. Omitted when the file has no
   * page: the docs reader tells a reader that the page they asked for was
   * never written, which is a fine answer to a question they asked and a poor
   * one to a link they were offered. Rule 21.
   *
   * Named for the surface the nav names ("Docs"), not for the tab. The tab is
   * "Documentation" and sits forty pixels below this; two controls with near
   * enough the same name, one staying on the page and one leaving it, is the
   * two-verbs-one-subject problem relocated rather than fixed.
   */
  wikiHref?: string | undefined;
  LinkComponent?: React.ElementType | undefined;
}

/**
 * The file page's opening: the house micro-label eyebrow, the path as identity,
 * and a `PageLede`-shaped figure carrying the defect score with its canonical
 * band.
 *
 * This is a **server** component and stays one. It was `EntityHeader` before,
 * whose eyebrow ran `text-xs font-semibold uppercase tracking-wider` against
 * the house `font-mono text-[10px] uppercase tracking-[0.12em]`, and whose
 * identity ran `text-lg` against `RepoIdentityHeader`'s `text-xl sm:text-2xl` —
 * a different face and two different sizes from every other surface.
 *
 * The score is banded by `bandForScore`, not `scoreBadgeClass`. The latter is a
 * four-step presentation ramp whose own docstring says it is not a labelling
 * scheme, and it disagrees with the bands the Files index, the treemap and the
 * health map all paint: a 6.9 read one way here and another 200px away on the
 * map that links to this page.
 */
export function FilePageHeader({
  data,
  linkPrefix,
  wikiHref,
  LinkComponent,
}: FilePageHeaderProps) {
  const A = LinkComponent ?? "a";
  const score = data.health.metric?.score;
  const dir = data.file_path.slice(0, data.file_path.lastIndexOf("/") + 1);
  const name = data.file_path.slice(dir.length);

  return (
    <header className="flex flex-col gap-6">
      <div>
        {/* Eyebrow left, the one door out right. The pairing is deliberate:
            both are page-level, neither is about the file's numbers, and
            putting the link here means it is on screen from whichever tab you
            arrive on rather than only from the one that duplicates it. */}
        <div className="flex items-baseline justify-between gap-4">
          <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            File
          </p>
          {wikiHref && (
            <A
              href={wikiHref}
              className="shrink-0 text-sm font-medium text-[var(--color-accent-primary)] hover:underline"
            >
              Read in Docs <span aria-hidden>&rarr;</span>
            </A>
          )}
        </div>
        {/* `break-all`, never an ellipsis: a path is the identity of this page
            and rule 6 puts no truncation in the primary column. The directory
            is set quieter than the leaf so a deep path still reads as one
            thing you can find your place in. */}
        <h1 className="mt-2 font-mono text-xl font-semibold tracking-tight text-[var(--color-text-primary)] break-all sm:text-2xl">
          {dir && <span className="font-normal text-[var(--color-text-tertiary)]">{dir}</span>}
          {name}
        </h1>
      </div>

      {score != null ? (
        <PageLede
          label="Defect risk"
          value={score.toFixed(1)}
          valueColor={healthBandInk(bandForScore(score))}
          unit="out of 10"
          band={{
            label: HEALTH_BAND_LABEL[bandForScore(score)],
            color: healthBandInk(bandForScore(score)),
          }}
          layout="beside"
        >
          <FileProse data={data} linkPrefix={linkPrefix} LinkComponent={A} />
        </PageLede>
      ) : (
        <div className="max-w-[62ch] text-[13px] leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
          <FileProse data={data} linkPrefix={linkPrefix} LinkComponent={A} />
        </div>
      )}

      <FileMarks data={data} />

      <StatRibbon stats={headerStats(data)} {...(LinkComponent ? { LinkComponent } : {})} />
    </header>
  );
}

/** The sentences that make the figure mean something. Rule 4: the prose is
 *  load-bearing here, not decoration. */
function FileProse({
  data,
  linkPrefix,
  LinkComponent,
}: {
  data: FileDetailResponse;
  linkPrefix: string;
  LinkComponent: React.ElementType;
}) {
  const A = LinkComponent;
  const findings = data.health.findings.length;
  const deduction = data.health.breakdown?.total_deduction;
  const owner = data.git?.primary_owner;
  const ownerPct = data.git?.primary_owner_commit_pct;
  const commits = data.git?.commit_count_total ?? 0;

  return (
    <>
      <p>{fileSummary(data)}</p>
      {findings > 0 && (
        <p className="mt-3">
          {findings === 1 ? "One open finding deducts" : `${formatNumber(findings)} open findings deduct`}
          {deduction != null ? ` ${deduction.toFixed(2)}` : ""} from the score, which is
          calibrated against this file&rsquo;s complexity, static biomarkers and prior fix
          history.
        </p>
      )}
      {owner && (
        <p className="mt-3">
          Mostly written by{" "}
          <A
            href={`${linkPrefix}/owners/${encodeURIComponent(owner)}`}
            className="text-[var(--color-accent-primary)] hover:underline"
          >
            {owner}
          </A>
          {ownerPct != null && ` (${Math.round(ownerPct * 100)}% of commits)`}
          {commits > 0 && `, across ${formatNumber(commits)} commit${commits === 1 ? "" : "s"}`}.
        </p>
      )}
    </>
  );
}

/** The headline figures, as a hairline `<dl>` rather than four bordered tiles.
 *  Only figures the payload actually carries — an unmeasured cell is dropped,
 *  not printed as an em dash. */
function headerStats(data: FileDetailResponse): RibbonStat[] {
  const stats: RibbonStat[] = [];
  const nloc = data.health.metric?.nloc;
  // `data.symbols.length`, not `graph.symbol_count`: the latter is a column on
  // the graph node and the former is the extracted-symbol table, and they can
  // disagree — which read as "Symbols 12" here over "All 9 extracted symbols"
  // one section below. This page can enumerate the second one, so it counts it.
  const symbols = data.symbols.length;
  const coveragePct = data.coverage?.line_coverage_pct ?? data.health.metric?.line_coverage_pct;

  if (nloc != null) stats.push({ label: "Lines", value: formatLOC(nloc) });
  if (symbols > 0) stats.push({ label: "Symbols", value: formatNumber(symbols) });
  if (data.graph) {
    stats.push({ label: "Depended on by", value: formatNumber(data.graph.in_degree) });
    stats.push({ label: "Depends on", value: formatNumber(data.graph.out_degree) });
  }
  if (coveragePct != null) {
    stats.push({
      label: "Tests",
      value: `${coveragePct.toFixed(0)}%`,
      valueColor: coverageTextColor(coveragePct),
    });
  }
  return stats;
}

/** Synthesised "what is this" one-liner for a file — wiki summary or fallback. */
export function fileSummary(data: FileDetailResponse): string {
  const wiki = data.wiki_page?.summary?.trim();
  if (wiki) return wiki;
  const lang = data.graph?.language;
  const symbols = data.symbols.length;
  const name = data.file_path.split("/").pop() ?? data.file_path;
  const langPart = lang ? `${lang} ` : "";
  return `Undocumented ${langPart}file ${name}${
    symbols ? ` — ${symbols} symbol${symbols === 1 ? "" : "s"}` : ""
  }.`;
}
