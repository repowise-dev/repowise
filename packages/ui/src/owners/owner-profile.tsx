import * as React from "react";
import type {
  OwnerProfile,
  OwnerFileEntry,
  OwnerModuleRollup,
  OwnerCoAuthor,
} from "@repowise-dev/types/owners";
import { PageLede } from "../shared/page-lede";
import { OverviewSection, SectionLink } from "../overview/section";
import { ReadsColumn, type ReadItem } from "../overview/reads-column";
import { StatRibbon, type RibbonStat } from "../stats/stat-ribbon";
import { EmptyState } from "../shared/empty-state";
import { rampStep } from "../lib/ramp";
import { formatCompact, formatRelativeTimeOrNull } from "../lib/format";
import { OwnerAvatar } from "./owner-avatar";

/**
 * The contributor profile, rebuilt on the section style.
 *
 * It was nine bordered containers at near-identical weight — a header card, a
 * four-tile risk strip, and five more cards in a 2 + 1 grid — so the page had
 * nothing to lead with and no way to say which of the nine mattered.
 *
 * Three things drove the rewrite, all of them measured against a real index
 * before being changed rather than after:
 *
 * 1. **Every marker fired on nearly every row.** The hotspot flame hit 80% /
 *    70% / 55% of the top files for the three contributors with real history;
 *    the churn pill's red band hit 80% / 85% / 55%; the bus-factor scale's
 *    green band had no members at all. A badge most rows carry says nothing.
 *    The one marker left is hotspot *and* sole owner *and* ≥90% of the file's
 *    commits, which lands on 25% / 25% / 15% and names a single thing: a
 *    high-churn file this person alone maintains.
 *
 * 2. **Colour was doing work it is not allowed to do.** Lines added rendered
 *    green and lines deleted red, as though deleting code were an error; churn
 *    percentile and bus factor got the health triad for what are plain counts.
 *    Green / amber / red belong to health bands. What is left uses the accent
 *    ramp, where position carries magnitude.
 *
 * 3. **The figures restated each other.** Modules was a headline tile *and* a
 *    section; co-authors was a headline tile *and* a section. Each figure in
 *    this file now appears exactly once, in the place that can explain it:
 *    sole ownership in the lede, the three secondary reads in the column beside
 *    it, five volume figures in the ribbon, and the rest inside the section
 *    that owns the subject.
 *
 * Server-renderable: no state, no handlers, `hrefFor*` instead of `onSelect*`.
 * Every row is then a real URL that survives a middle-click, and the page above
 * it stops needing a client boundary.
 */

export interface OwnerProfileViewProps {
  owner: OwnerProfile;
  /** `/repos/{id}` — the prefix for every drill-in on this page. */
  base: string;
  hrefForFile?: ((filePath: string) => string) | undefined;
  hrefForModule?: ((modulePath: string) => string) | undefined;
  hrefForCoAuthor?: ((coAuthor: OwnerCoAuthor) => string) | undefined;
  LinkComponent?: React.ElementType | undefined;
}

/**
 * A break opportunity after each path separator.
 *
 * Without it a 72-character path in a narrow column breaks mid-identifier and
 * wraps to ten lines; with it, it breaks between directories and wraps to two.
 * `<wbr>` rather than a zero-width space so the path is still clean when it is
 * copied out of the page.
 */
function PathText({ path }: { path: string }) {
  const segments = path.split("/");
  return (
    <span className="font-mono text-xs text-[var(--color-text-primary)] transition-colors group-hover:text-[var(--color-accent-primary)] [overflow-wrap:anywhere]">
      {segments.map((seg, i) => (
        <React.Fragment key={i}>
          {i > 0 && (
            <>
              /<wbr />
            </>
          )}
          {seg}
        </React.Fragment>
      ))}
    </span>
  );
}

/**
 * The only marker on the file table.
 *
 * Sole ownership on its own marks 80% of this contributor's files, and a
 * hotspot flag 80% again, so either alone marks nothing. The conjunction — a
 * high-churn file, no second author, and this person wrote at least 90% of its
 * commits — lands on a quarter of rows at most and describes one specific
 * situation worth acting on.
 */
function isSoleCarried(f: OwnerFileEntry): boolean {
  return f.is_hotspot && f.bus_factor <= 1 && (f.primary_owner_commit_pct ?? 0) >= 0.9;
}

const pct = (part: number, whole: number): number =>
  whole > 0 ? Math.round((part / whole) * 100) : 0;

export function OwnerProfileView({
  owner,
  base,
  hrefForFile,
  hrefForModule,
  hrefForCoAuthor,
  LinkComponent,
}: OwnerProfileViewProps) {
  const solePct = pct(owner.bus_factor_risk_files, owner.files_owned);
  const filesShown = owner.top_files.slice(0, 20);
  const soleCarried = filesShown.filter(isSoleCarried).length;

  // Every read here is a figure that appears nowhere else on the page, and
  // every one is a link: this column doubles as navigation for someone who
  // opened the profile for one specific thing.
  const reads: ReadItem[] = [];
  if (owner.dead_code_files_owned > 0) {
    reads.push({
      key: "dead",
      label: "Dead code owned",
      value: owner.dead_code_files_owned.toLocaleString(),
      unit: "files",
      why: `${owner.dead_code_lines_owned.toLocaleString()} unreachable lines still attributed here. Usually the cheapest thing on this page to act on.`,
      href: `${base}/code-health?tab=dead-code`,
    });
  }
  if (owner.silo_modules > 0) {
    reads.push({
      key: "silos",
      label: "Silo directories",
      value: owner.silo_modules.toLocaleString(),
      unit: owner.silo_modules === 1 ? "directory" : "directories",
      why: "Top-level directories where this person owns more than 80% of the files, so a review has nobody obvious to route to.",
      href: `${base}/owners`,
    });
  }
  if (owner.agent_collab && owner.agent_collab.agent_commit_count > 0) {
    const a = owner.agent_collab;
    reads.push({
      key: "agent",
      label: "Agent-assisted",
      value: a.agent_commit_count.toLocaleString(),
      unit: "commits",
      why:
        (a.agent_share_pct != null
          ? `${a.agent_share_pct.toFixed(2)}% of the commits on their owned files carry a coding-agent trailer. `
          : "Read from commit trailers, so it counts what was declared. ") +
        `${a.files_with_agent_commits.toLocaleString()} owned ${a.files_with_agent_commits === 1 ? "file has" : "files have"} one.`,
      href: `${base}/commits`,
    });
  }

  // Volume figures, none of which appear in the lede, the reads column or a
  // section below. The two line counts are the payload's `_est` fields, so an
  // exact six-digit figure would claim a precision the number does not have.
  const ribbon: RibbonStat[] = [
    { label: "Commits, 90d", value: owner.commit_count_90d.toLocaleString() },
    {
      label: "Lines added",
      value: formatCompact(owner.lines_added_90d_est),
      hint: "Estimated from commit stats over the last 90 days",
    },
    {
      label: "Lines removed",
      value: formatCompact(owner.lines_deleted_90d_est),
      hint: "Estimated from commit stats over the last 90 days",
    },
    {
      label: "Files touched",
      value: (owner.files_touched_total ?? owner.top_files.length).toLocaleString(),
      hint: "Files with at least one commit attributed to this person",
    },
    {
      label: "High-churn owned",
      value: owner.hotspots_owned.toLocaleString(),
      hint: "Owned files that are also in the repo's high-churn band",
    },
  ];

  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      <section className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-10">
        <PageLede
          label="Sole-owned files"
          value={owner.bus_factor_risk_files.toLocaleString()}
          unit={`of ${owner.files_owned.toLocaleString()} owned`}
          action={
            <SectionLink href={`${base}/owners`} LinkComponent={LinkComponent}>
              All contributors
            </SectionLink>
          }
        >
          <p>
            {owner.bus_factor_risk_files > 0 ? (
              <>
                <strong className="font-semibold text-[var(--color-text-primary)]">
                  {owner.bus_factor_risk_files.toLocaleString()} files
                </strong>{" "}
                where this person wrote most of the surviving lines and nobody
                else has committed, which is {solePct}% of everything they own.
                Ownership follows the surviving lines rather than the last
                commit, so this is knowledge held rather than activity logged.
              </>
            ) : (
              <>
                Every one of the{" "}
                {owner.files_owned.toLocaleString()} files this person owns has a
                second author. Ownership follows the surviving lines rather than
                the last commit, so this is knowledge held rather than activity
                logged.
              </>
            )}
          </p>
        </PageLede>
        <ReadsColumn items={reads} LinkComponent={LinkComponent} />
      </section>

      <StatRibbon stats={ribbon} />

      <OwnershipFootprint
        modules={owner.modules}
        base={base}
        hrefForModule={hrefForModule}
        LinkComponent={LinkComponent}
      />

      <OverviewSection
        title="Files they carry"
        description={`The ${filesShown.length} ${filesShown.length === 1 ? "file" : "files"} with the most commits attributed to this person, of ${(owner.files_touched_total ?? owner.top_files.length).toLocaleString()} touched. Churn is a percentile against every file in the repo. A dot marks a high-churn file they alone maintain and wrote at least 90% of the commits for${soleCarried > 0 ? `: ${soleCarried} of these ${filesShown.length}` : ", and none of these qualify"}.`}
      >
        <OwnerFileTable
          files={filesShown}
          hrefForFile={hrefForFile}
          LinkComponent={LinkComponent}
        />
      </OverviewSection>

      <WorkingRelationships
        coAuthors={owner.co_authors}
        total={owner.co_authors_total ?? owner.co_authors.length}
        hrefForCoAuthor={hrefForCoAuthor}
        LinkComponent={LinkComponent}
      />

      <CommitMix categories={owner.commit_categories} />
    </div>
  );
}

/* -------------------------------------------------------------------------- */

/**
 * Where the ownership sits, as one stacked bar rather than a row of bars.
 *
 * The endpoint only attributes top-level directories, so this list is two to
 * four entries on a real repo — this one has `packages`, `tests`, `scripts`
 * and `docker`. Four rows each carrying a label, a count, a track and a
 * percentage is more chrome than four numbers can pay for, and it invites the
 * reader to compare bars that are all measuring different denominators. One
 * bar shows the split, and the key carries the share.
 *
 * The ramp, not a hue per directory: these are ordered shares of one whole, so
 * position is the encoding. Five unrelated colours for five directories is the
 * language-donut anti-pattern.
 */
function OwnershipFootprint({
  modules,
  base,
  hrefForModule,
  LinkComponent,
}: {
  modules: OwnerModuleRollup[];
  base: string;
  hrefForModule?: ((modulePath: string) => string) | undefined;
  LinkComponent?: React.ElementType | undefined;
}) {
  const A = LinkComponent ?? "a";
  const ordered = [...modules].sort((a, b) => b.file_count - a.file_count);
  const total = ordered.reduce((sum, m) => sum + m.file_count, 0);

  return (
    <OverviewSection
      title="Where the ownership sits"
      description="Share is the fraction of each directory's files where this person wrote most of the surviving lines. Only top-level directories are attributed, so this is a coarse map rather than a per-module one."
      action={
        <SectionLink href={`${base}/owners`} LinkComponent={LinkComponent}>
          Ownership
        </SectionLink>
      }
    >
      {total === 0 ? (
        <EmptyState
          className="p-6"
          title="No directory attribution yet"
          description="Ownership by directory appears after the next git sync."
        />
      ) : (
        <>
          <div className="flex h-2 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
            {ordered.map((m, i) => (
              <span
                key={m.module_path}
                title={`${m.module_path}: ${m.file_count.toLocaleString()} owned files`}
                className="h-full"
                style={{
                  width: `${(m.file_count / total) * 100}%`,
                  background: rampStep(i),
                }}
              />
            ))}
          </div>
          <ul className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:gap-x-6 sm:gap-y-2">
            {ordered.map((m, i) => {
              const href = hrefForModule?.(m.module_path);
              const label = (
                <>
                  <span
                    aria-hidden
                    className="h-2 w-2 shrink-0 rounded-[2px]"
                    style={{ background: rampStep(i) }}
                  />
                  <span className="font-mono text-xs text-[var(--color-text-primary)] transition-colors group-hover:text-[var(--color-accent-primary)] [overflow-wrap:anywhere]">
                    {m.module_path}
                  </span>
                  <span className="text-xs tabular-nums text-[var(--color-text-tertiary)]">
                    {m.file_count.toLocaleString()}{" "}
                    {m.file_count === 1 ? "file" : "files"} ·{" "}
                    {Math.round(m.dominant_pct * 100)}% theirs
                  </span>
                </>
              );
              return (
                <li key={m.module_path} className="min-w-0">
                  {href ? (
                    <A href={href} className="group flex items-center gap-2 no-underline">
                      {label}
                    </A>
                  ) : (
                    <span className="flex items-center gap-2">{label}</span>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </OverviewSection>
  );
}

/* -------------------------------------------------------------------------- */

/**
 * The file table.
 *
 * Hand-rolled rather than `ResponsiveTable` for the same reason `OwnerTable`
 * is: that primitive is a client component, and nothing here needs one.
 *
 * Below `sm` it stops being a table. Five columns cannot share 390px without
 * starving the primary one, and the primary one is a file path that may not be
 * truncated — so the path takes the full width and the four figures collapse
 * into a single meta line beneath it.
 */
function OwnerFileTable({
  files,
  hrefForFile,
  LinkComponent,
}: {
  files: OwnerFileEntry[];
  hrefForFile?: ((filePath: string) => string) | undefined;
  LinkComponent?: React.ElementType | undefined;
}) {
  const A = LinkComponent ?? "a";

  if (files.length === 0) {
    return (
      <EmptyState
        className="p-6"
        title="No file attribution yet"
        description="File-level ownership appears after the next git sync."
      />
    );
  }

  const headCls =
    "border-b border-[var(--color-border-default)] pb-2 font-mono text-[10px] font-normal uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";
  const numCls = "whitespace-nowrap py-2.5 pl-3 text-right align-top tabular-nums max-sm:hidden";

  return (
    <div className="-mx-[var(--page-pad)] overflow-x-auto px-[var(--page-pad)] sm:mx-0 sm:px-0">
      <table className="w-full border-collapse text-[13.5px]">
        <thead className="max-sm:hidden">
          <tr>
            <th scope="col" className={`${headCls} pr-3 text-left`}>
              File
            </th>
            <th scope="col" className={`${headCls} whitespace-nowrap pl-3 text-right`}>
              Commits 90d
            </th>
            <th
              scope="col"
              title="Percentile rank of this file's change frequency against every file in the repo"
              className={`${headCls} cursor-help whitespace-nowrap pl-3 text-right`}
            >
              Churn
            </th>
            <th
              scope="col"
              title="Share of this file's commits written by this person"
              className={`${headCls} cursor-help whitespace-nowrap pl-3 text-right`}
            >
              Their commits
            </th>
            <th scope="col" className={`${headCls} whitespace-nowrap pl-3 text-right`}>
              Touched
            </th>
          </tr>
        </thead>
        <tbody>
          {files.map((f) => {
            const href = hrefForFile?.(f.file_path);
            const ownPct =
              f.primary_owner_commit_pct != null
                ? `${Math.round(f.primary_owner_commit_pct * 100)}%`
                : "—";
            const touched = formatRelativeTimeOrNull(f.last_commit_at) ?? "—";
            return (
              <tr
                key={f.file_path}
                className="border-b border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)] max-sm:block"
              >
                <td className="py-2.5 pr-3 align-top max-sm:block max-sm:pr-0">
                  {href ? (
                    <A href={href} className="group no-underline">
                      <PathText path={f.file_path} />
                    </A>
                  ) : (
                    <PathText path={f.file_path} />
                  )}
                  {isSoleCarried(f) && (
                    <span
                      title="High-churn file this person alone maintains"
                      className="ml-1.5 inline-block h-[5px] w-[5px] shrink-0 rounded-full bg-[var(--color-warning)] align-middle"
                    />
                  )}
                  {/* The four figures as one line, below `sm` only. Reads as a
                      sentence rather than as a table row that lost its header. */}
                  <span className="mt-1 hidden text-[11.5px] tabular-nums text-[var(--color-text-tertiary)] max-sm:block">
                    {f.commit_count_90d.toLocaleString()} commits in 90d · churn{" "}
                    {Math.round(f.churn_percentile)} · {ownPct} of its commits ·
                    touched {touched}
                  </span>
                </td>
                <td className={numCls}>{f.commit_count_90d.toLocaleString()}</td>
                <td className={`${numCls} text-[var(--color-text-tertiary)]`}>
                  {Math.round(f.churn_percentile)}
                </td>
                <td className={`${numCls} text-[var(--color-text-tertiary)]`}>{ownPct}</td>
                <td className={`${numCls} text-[var(--color-text-tertiary)]`}>{touched}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* -------------------------------------------------------------------------- */

/**
 * Who else works in the same files.
 *
 * A list of ten was the wrong shape for this data. Overlap on a real repo is
 * one partner and then a cliff — 15.8%, then 1.6%, 1.2%, 1.0%, 0.8% — so rows
 * two through ten are people who once touched the same file, presented at the
 * same weight as the person who actually shares the work. The lead partner is
 * the answer to the question the section exists for (who reviews this), and
 * the tail is honestly reported as a count rather than padded out into rows.
 */
function WorkingRelationships({
  coAuthors,
  total,
  hrefForCoAuthor,
  LinkComponent,
}: {
  coAuthors: OwnerCoAuthor[];
  total: number;
  hrefForCoAuthor?: ((coAuthor: OwnerCoAuthor) => string) | undefined;
  LinkComponent?: React.ElementType | undefined;
}) {
  const A = LinkComponent ?? "a";
  const [lead, second] = [...coAuthors].sort(
    (a, b) => b.co_change_strength - a.co_change_strength,
  );

  return (
    <OverviewSection
      title="Who else works here"
      description="Contributors editing the same files. Overlap is the share of this person's files the other has also committed to, so it reads as how much of their work is genuinely shared."
    >
      {!lead ? (
        <EmptyState
          className="p-6"
          title="Nobody else has touched these files"
          description="Co-authorship appears once a second contributor commits to a file this person owns."
        />
      ) : (
        <div className="flex flex-col gap-3">
          <LeadPartner
            coAuthor={lead}
            href={hrefForCoAuthor?.(lead)}
            LinkComponent={A}
          />
          <p className="max-w-[62ch] text-xs leading-relaxed text-[var(--color-text-tertiary)] [text-wrap:pretty]">
            {total <= 1 ? (
              <>No other contributor shares a file with this person.</>
            ) : second ? (
              <>
                The next closest of {(total - 1).toLocaleString()} other
                contributors overlaps on{" "}
                {(second.co_change_strength * 100).toFixed(1)}%, so there is one
                natural reviewer here rather than a shortlist.
              </>
            ) : (
              <>
                {(total - 1).toLocaleString()} other contributors share at least
                one file.
              </>
            )}
          </p>
        </div>
      )}
    </OverviewSection>
  );
}

function LeadPartner({
  coAuthor,
  href,
  LinkComponent,
}: {
  coAuthor: OwnerCoAuthor;
  href: string | undefined;
  LinkComponent: React.ElementType;
}) {
  const A = LinkComponent;
  const body = (
    <>
      <OwnerAvatar name={coAuthor.name} email={coAuthor.email} size="md" />
      <span className="min-w-0">
        {/* No truncation: the name is the primary column here and there is room
            for it. */}
        <span className="block font-medium text-[var(--color-text-primary)] transition-colors group-hover:text-[var(--color-accent-primary)] [text-wrap:pretty]">
          {coAuthor.name || coAuthor.email || "unknown"}
        </span>
        <span className="block text-xs tabular-nums text-[var(--color-text-tertiary)]">
          {coAuthor.shared_files.toLocaleString()} shared{" "}
          {coAuthor.shared_files === 1 ? "file" : "files"} ·{" "}
          {(coAuthor.co_change_strength * 100).toFixed(1)}% of this person&apos;s
          work overlaps theirs
        </span>
      </span>
    </>
  );

  return href ? (
    <A
      href={href}
      className="group flex w-fit items-center gap-3 no-underline"
    >
      {body}
    </A>
  ) : (
    <span className="flex items-center gap-3">{body}</span>
  );
}

/* -------------------------------------------------------------------------- */

/**
 * Commit mix, down the accent ramp.
 *
 * The colour map this replaced keyed on `feat`, `docs`, `test`, `chore` and
 * `perf`. The endpoint returns `feature`, `fix`, `refactor` and `dependency`,
 * so four of the five colours were unreachable and the largest category fell
 * through to the default — a palette that had never once rendered as designed.
 * Ordered shares of one whole want the ramp, where position is the magnitude,
 * not a hue per category.
 *
 * Zero-count categories are dropped rather than rendered as an empty track:
 * the endpoint returns the full key set, so a contributor who has never
 * refactored was getting a labelled bar with nothing in it.
 */
function CommitMix({ categories }: { categories: Record<string, number> }) {
  const entries = Object.entries(categories ?? {})
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((sum, [, n]) => sum + n, 0);

  return (
    <OverviewSection
      title="What they commit"
      description={
        total > 0
          ? `${total.toLocaleString()} commits classified from the subject line, across every file this person has touched.`
          : "Commits are classified from the subject line during indexing."
      }
    >
      {entries.length === 0 ? (
        <EmptyState
          className="p-6"
          title="No classified commits yet"
          description="Commit classification runs during indexing and fills this in on the next sync."
        />
      ) : (
        <dl className="flex max-w-[420px] flex-col gap-3">
          {entries.map(([category, n], i) => (
            <div key={category}>
              <div className="flex items-baseline justify-between gap-3 text-xs">
                <dt className="capitalize text-[var(--color-text-secondary)]">{category}</dt>
                <dd className="tabular-nums text-[var(--color-text-tertiary)]">
                  {n.toLocaleString()} · {pct(n, total)}%
                </dd>
              </div>
              <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
                <span
                  className="block h-full"
                  style={{ width: `${(n / total) * 100}%`, background: rampStep(i) }}
                />
              </div>
            </div>
          ))}
        </dl>
      )}
    </OverviewSection>
  );
}
