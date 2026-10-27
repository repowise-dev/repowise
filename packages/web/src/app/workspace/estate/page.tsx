import type { Metadata } from "next";
import type { ReactNode } from "react";
import { getTranslations } from "next-intl/server";
import { FileText } from "lucide-react";
import type {
  EstateCheck,
  EstateDanglingCall,
  EstateEdge,
  EstateReport,
  EstateRepositoryRow,
  EstateSeam,
  EstateSection,
  EstateStartFile,
} from "@repowise-dev/types/estate";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { Callout } from "@repowise-dev/ui/shared/callout";
import { OverviewSection } from "@repowise-dev/ui/overview/section";
import { StatRibbon, type RibbonStat } from "@repowise-dev/ui/stats/stat-ribbon";
import { healthMark } from "@repowise-dev/ui/health/tokens";
import { formatDate, formatNumber } from "@repowise-dev/ui/lib/format";
import { getWorkspaceEstate } from "@/lib/api/workspace";
import { EstateTable } from "./estate-table";
import {
  checkData,
  findCheck,
  graphEvidence,
  missingGraph,
  repoRows,
  type EstateRepoRow,
} from "./estate-model";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("estate");
  return { title: t("title") };
}

export const revalidate = 30;

/** The minimal translator shape the helpers below need; next-intl's `t` fits. */
type Translator = ((key: string, values?: Record<string, string | number>) => string) & {
  has: (key: string) => boolean;
};

/** Rows a table shows before it says how many it left out; the JSON keeps every one. */
const DEPENDENCY_ROWS = 20;
const DANGLING_ROWS = 25;
const SEAM_ROWS = 10;
const START_SERVICE_ROWS = 10;

const SECTION_KEYS: Record<string, string> = {
  "estate-summary": "summary",
  "estate-map": "map",
  "estate-contracts": "contracts",
  "estate-health": "health",
  "estate-seams": "seams",
  "estate-start": "start",
};

/** Checks the health table below already shows row by row. */
const IN_HEALTH_TABLE = new Set(["health", "churn-hotspots", "bug-magnets"]);

const mono = "font-mono text-[11px] text-[var(--color-text-primary)] [overflow-wrap:anywhere]";
const quiet = "text-[var(--color-text-tertiary)]";

function short(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 12) : "";
}

function indexStateLabel(t: Translator, state: "behind" | "unreadable"): string {
  return state === "behind" ? t("indexBehind") : t("indexUnreadable");
}

function shownOf(t: Translator, shown: number, total: number): string | undefined {
  return total > shown ? t("shownOf", { shown, total }) : undefined;
}

/**
 * The estate report for the workspace: the same document
 * `repowise report estate` prints, one section per report section.
 *
 * Sentences from the engine (each check's reason and notes) are shown as
 * written, in English; the page's own labels are translated.
 */
export default async function EstatePage() {
  const t = (await getTranslations("estate")) as unknown as Translator;

  let report: EstateReport | null = null;
  let error = "";
  try {
    report = await getWorkspaceEstate();
  } catch (err) {
    error = err instanceof Error ? err.message : String(err);
  }

  if (!report) {
    return (
      <PageShell title={t("title")} icon={<FileText />} description={t("description")}>
        <EmptyState
          tone="error"
          title={t("unavailableTitle")}
          description={t("unavailableDescription", { error })}
        />
      </PageShell>
    );
  }

  const noGraph = missingGraph(report);
  const evidence = graphEvidence(report);
  const repos = report.repos ?? [];
  const indexed = repos.filter((r) => (r.indexed ?? true) && !r.index_state).length;
  const behindIndex = repos.filter((r) => r.index_state === "behind").length;
  const repositories = checkData<EstateRepositoryRow[]>(
    findCheck(report, "estate-summary", "repositories"),
    "repositories",
    [],
  );
  const stale = repositories.filter(
    (r) => r.indexed && r.contracts_commit && r.indexed_commit && r.contracts_commit !== r.indexed_commit,
  ).length;
  const services = checkData<unknown[] | null>(
    findCheck(report, "estate-summary", "services"),
    "services",
    null,
  );
  const contracts = findCheck(report, "estate-contracts", "contracts");
  const links = Object.values(checkData<Record<string, number>>(contracts, "links_by_match_type", {}))
    .reduce((a, b) => a + b, 0);
  const dangling = findCheck(report, "estate-contracts", "dangling-calls");
  const architecture = findCheck(report, "estate-map", "architecture");

  const ribbon: RibbonStat[] = [
    {
      label: t("ribbon.repos"),
      value: formatNumber(repos.length),
      sub: t("ribbon.reposSub", { indexed, total: repos.length }),
    },
    {
      label: t("ribbon.services"),
      value: services ? formatNumber(services.length) : "",
      sub: t("ribbon.servicesSub"),
    },
    {
      label: t("ribbon.links"),
      value: contracts?.data ? formatNumber(links) : "",
      sub: t("ribbon.linksSub"),
    },
    {
      label: t("ribbon.dangling"),
      value: dangling
        ? formatNumber(checkData<EstateDanglingCall[]>(dangling, "dangling", []).length)
        : "",
      sub: t("ribbon.danglingSub"),
    },
    {
      label: t("ribbon.architecture"),
      value: !architecture
        ? ""
        : architecture.basis === "unresolved" || architecture.score == null
          ? t("ribbon.notDetermined")
          : architecture.score.toFixed(1),
      sub: t("ribbon.architectureSub"),
    },
  ];

  return (
    <PageShell title={t("title")} icon={<FileText />} description={t("description")}>
      {noGraph ? (
        <Callout tone="warning" label={t("noGraphLabel")}>
          {t("noGraphSentence")}
        </Callout>
      ) : evidence && evidence.state !== "measured" ? (
        <Callout
          tone="warning"
          label={evidence.state === "partial" ? t("evidencePartial") : t("evidenceInsufficient")}
        >
          {t(evidence.state === "partial" ? "evidencePartialSentence" : "evidenceInsufficientSentence", {
            reason: evidence.summary,
          })}
        </Callout>
      ) : null}
      {behindIndex > 0 ? (
        <Callout tone="warning" label={t("indexBehindLabel")}>
          {t("indexBehindSentence", { count: behindIndex })}
        </Callout>
      ) : null}
      {stale > 0 ? (
        <Callout tone="info" label={t("staleLabel")}>
          {t("staleSentence", { count: stale })}
        </Callout>
      ) : null}

      <StatRibbon stats={ribbon} />

      {(report.sections ?? []).map((section, i) => (
        <ReportSection key={`${section.id}:${section.repo ?? ""}`} section={section} report={report} t={t} flush={i === 0} />
      ))}

      <p className={`text-xs ${quiet}`}>
        {t("generated", {
          when: formatDate(report.generated_at),
          version: report.generator.version,
        })}{" "}
        <span lang="en">{report.notice}</span>
      </p>
    </PageShell>
  );
}

function ReportSection({
  section,
  report,
  t,
  flush,
}: {
  section: EstateSection;
  report: EstateReport;
  t: Translator;
  flush: boolean;
}) {
  const key = SECTION_KEYS[section.id];
  const title = key ? t(`section.${key}.title`) : section.title;
  const description = key ? t(`section.${key}.description`) : section.summary;
  const checks = section.checks ?? [];
  return (
    <OverviewSection id={section.id} title={title} description={description} flush={flush}>
      {section.failed ? (
        <Callout tone="error" label={t("failedLabel")}>
          {t("failedSentence")}
        </Callout>
      ) : null}
      {section.id === "estate-health" && !section.failed ? (
        <HealthTable rows={repoRows(report)} t={t} />
      ) : null}
      <div className="flex flex-col gap-6">
        {checks.map((check) => (
          <CheckBlock
            key={check.name}
            check={check}
            t={t}
            body={section.id === "estate-health" && IN_HEALTH_TABLE.has(check.name) ? null : checkBody(check, t)}
            showDetails={!(section.id === "estate-health" && IN_HEALTH_TABLE.has(check.name))}
          />
        ))}
      </div>
    </OverviewSection>
  );
}

/** One evidence item: its name and basis, the engine's sentence, then a table or its notes. */
function CheckBlock({
  check,
  t,
  body,
  showDetails,
}: {
  check: EstateCheck;
  t: Translator;
  body: ReactNode | null;
  showDetails: boolean;
}) {
  const label = t.has(`check.${check.name}`) ? t(`check.${check.name}`) : check.name;
  const details = check.details ?? [];
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <p className="flex flex-wrap items-baseline gap-x-2 font-mono text-[10px] uppercase tracking-[0.12em]">
        <span className="text-[var(--color-text-secondary)]">{label}</span>
        <span className={quiet}>{t(`basis.${check.basis}`)}</span>
      </p>
      <p lang="en" className="max-w-[80ch] text-[15px] leading-relaxed text-[var(--color-text-primary)] [text-wrap:pretty]">
        {check.reason}
      </p>
      {body ??
        (showDetails && details.length > 0 ? (
          <ul lang="en" className="m-0 flex list-none flex-col gap-1 p-0 text-xs text-[var(--color-text-secondary)]">
            {details.map((line, i) => (
              <li key={i} className="[overflow-wrap:anywhere]">
                {line}
              </li>
            ))}
          </ul>
        ) : null)}
    </div>
  );
}

/** The table a check reads as, or null to list its notes instead. */
function checkBody(check: EstateCheck, t: Translator): ReactNode | null {
  switch (check.name) {
    case "repositories":
      return <RepositoriesTable rows={checkData<EstateRepositoryRow[]>(check, "repositories", [])} t={t} />;
    case "dependencies":
      return <DependenciesTable edges={checkData<EstateEdge[]>(check, "edges", [])} t={t} />;
    case "contracts":
      return <ContractsTable check={check} t={t} />;
    case "dangling-calls":
      return <DanglingTable check={check} t={t} />;
    case "seams":
      return check.data ? <SeamsTable seams={checkData<EstateSeam[]>(check, "seams", [])} t={t} /> : null;
    case "start-services":
      return check.data ? <StartServicesTable reach={checkData<Record<string, number>>(check, "reach", {})} t={t} /> : null;
    case "start-files":
      return check.data ? (
        <StartFilesTable repos={checkData<Record<string, EstateStartFile[]>>(check, "repositories", {})} t={t} />
      ) : null;
    default:
      return null;
  }
}

function RepositoriesTable({ rows, t }: { rows: EstateRepositoryRow[]; t: Translator }) {
  if (rows.length === 0) return null;
  return (
    <EstateTable
      caption={t("check.repositories")}
      columns={[
        { label: t("col.repo") },
        { label: t("col.indexedAt") },
        { label: t("col.contractsAt") },
        { label: t("col.services") },
      ]}
      rows={rows.map((r) => {
        const behind = r.contracts_commit && r.indexed_commit && r.contracts_commit !== r.indexed_commit;
        return {
          key: r.alias,
          cells: [
            <span key="r" className="font-medium text-[var(--color-text-primary)]">{r.alias}</span>,
            r.index_state ? (
              <span key="i" className="text-[var(--color-warning)]">{indexStateLabel(t, r.index_state)}</span>
            ) : r.indexed ? (
              <span key="i" className={mono}>{short(r.indexed_commit)}</span>
            ) : (
              <span key="i" className={quiet}>{t("notIndexed")}</span>
            ),
            r.contracts_commit ? (
              <span key="c" className={mono}>
                {short(r.contracts_commit)}
                {behind ? <span className="ml-1.5 font-sans text-[10px] text-[var(--color-warning)]">{t("contractsBehind")}</span> : null}
              </span>
            ) : (
              <span key="c" className={quiet}>{t("notRead")}</span>
            ),
            r.services.length > 0 ? r.services.join(", ") : <span key="s" className={quiet}>{t("none")}</span>,
          ],
        };
      })}
    />
  );
}

function DependenciesTable({ edges, t }: { edges: EstateEdge[]; t: Translator }) {
  const structural = edges
    .filter((e) => e.structural)
    .sort((a, b) => b.weight - a.weight || a.source.localeCompare(b.source) || a.target.localeCompare(b.target));
  if (structural.length === 0) return null;
  const shown = structural.slice(0, DEPENDENCY_ROWS);
  return (
    <EstateTable
      caption={t("check.dependencies")}
      columns={[
        { label: t("col.dependency") },
        { label: t("col.kind") },
        { label: t("col.links"), numeric: true },
        { label: t("col.strategy") },
        { label: t("col.confidence"), numeric: true },
      ]}
      rows={shown.map((e) => ({
        key: `${e.source}->${e.target}:${e.kind}`,
        cells: [
          <span key="d" className="text-[var(--color-text-primary)]">
            {e.source} <span aria-hidden className={quiet}>→</span>
            <span className="sr-only">{t("dependsOn")}</span> {e.target}
          </span>,
          <span key="k" className="font-mono text-[11px]">{e.kind}</span>,
          formatNumber(e.weight),
          <span key="s" className="font-mono text-[11px]">{e.strategy ?? e.match_type}</span>,
          e.confidence.toFixed(2),
        ],
      }))}
      note={shownOf(t, shown.length, structural.length)}
    />
  );
}

function ContractsTable({ check, t }: { check: EstateCheck; t: Translator }) {
  const providers = checkData<Record<string, number>>(check, "providers_by_type", {});
  const consumers = checkData<Record<string, number>>(check, "consumers_by_type", {});
  const linked = checkData<Record<string, number>>(check, "links_by_type", {});
  const types = [...new Set([...Object.keys(providers), ...Object.keys(consumers)])].sort();
  if (types.length === 0) return null;
  const orphans = checkData<number>(check, "orphan_providers", 0);
  const serviceLinks = checkData<number>(check, "service_links", 0);
  const notes = [
    serviceLinks > 0 ? t("serviceLinksNote", { count: serviceLinks }) : "",
    orphans > 0 ? t("orphanNote", { count: orphans }) : "",
  ].filter(Boolean);
  return (
    <EstateTable
      caption={t("check.contracts")}
      columns={[
        { label: t("col.type") },
        { label: t("col.providers"), numeric: true },
        { label: t("col.consumers"), numeric: true },
        { label: t("col.links"), numeric: true },
      ]}
      rows={types.map((type) => ({
        key: type,
        cells: [
          <span key="t" className="font-mono text-[11px] text-[var(--color-text-primary)]">{type}</span>,
          formatNumber(providers[type] ?? 0),
          formatNumber(consumers[type] ?? 0),
          formatNumber(linked[type] ?? 0),
        ],
      }))}
      note={notes.join(" ") || undefined}
    />
  );
}

function DanglingTable({ check, t }: { check: EstateCheck; t: Translator }) {
  const rows = checkData<EstateDanglingCall[]>(check, "dangling", []);
  const external = checkData<number>(check, "external", 0);
  const externalNote = external > 0 ? t("externalNote", { count: external }) : undefined;
  if (rows.length === 0) {
    return externalNote ? <p className={`text-xs ${quiet}`}>{externalNote}</p> : null;
  }
  const shown = rows.slice(0, DANGLING_ROWS);
  return (
    <EstateTable
      caption={t("check.dangling-calls")}
      columns={[
        { label: t("col.consumer") },
        { label: t("col.file") },
        { label: t("col.target") },
        { label: t("col.contract") },
        { label: t("col.why") },
      ]}
      rows={shown.map((r) => ({
        key: `${r.consumer}:${r.file}:${r.contract_id}`,
        cells: [
          <span key="c" className="font-medium text-[var(--color-text-primary)]">{r.consumer}</span>,
          <span key="f" className={mono}>{r.line ? `${r.file}:${r.line}` : r.file}</span>,
          r.target ?? <span key="t" className={quiet}>{t("targetUnresolved")}</span>,
          <span key="i" className={mono}>{r.contract_id}</span>,
          t.has(`danglingReason.${r.reason}`) ? t(`danglingReason.${r.reason}`) : r.reason,
        ],
      }))}
      note={[shownOf(t, shown.length, rows.length), externalNote].filter(Boolean).join(" ") || undefined}
    />
  );
}

/** The three-band mark beside each repo's health, as every health mark draws it. */
function HealthMark({ score, t }: { score: number; t: Translator }) {
  const mark = healthMark(score);
  return (
    <span className="inline-flex items-center gap-1.5">
      <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: mark.color }} />
      <span className="font-mono font-semibold tabular-nums text-[var(--color-text-primary)]">{mark.value}</span>
      <span className="text-xs text-[var(--color-text-secondary)]">{t(`band.${mark.band}`)}</span>
    </span>
  );
}

function HealthTable({ rows, t }: { rows: EstateRepoRow[]; t: Translator }) {
  if (rows.length === 0) return null;
  const dash = <span className={quiet} title={t("notMeasured")}>—</span>;
  const fixed = (n: number | null | undefined) => (n == null ? dash : n.toFixed(1));
  return (
    <EstateTable
      caption={t("section.health.title")}
      columns={[
        { label: t("col.repo") },
        { label: t("col.health") },
        { label: t("col.maintainability"), numeric: true },
        { label: t("col.hotspotHealth"), numeric: true },
        { label: t("col.churnHotspots"), numeric: true },
        { label: t("col.bugMagnets"), numeric: true },
        { label: t("col.snapshot") },
      ]}
      rows={rows.map((r) => {
        const h = r.health;
        const staleSnapshot = h && h.store_commit && h.index_commit && h.store_commit !== h.index_commit;
        return {
          key: r.repo,
          cells: [
            <span key="r" className="font-medium text-[var(--color-text-primary)]">{r.repo}</span>,
            h ? (
              <HealthMark key="h" score={h.average_health} t={t} />
            ) : (
              <span key="h" className={quiet}>
                {r.indexState ? indexStateLabel(t, r.indexState) : r.indexed ? t("noSnapshot") : t("notIndexed")}
              </span>
            ),
            fixed(h?.maintainability_average),
            fixed(h?.hotspot_health),
            r.churn ? formatNumber(r.churn.hotspots ?? 0) : dash,
            r.magnets ? (
              <span key="m">
                {formatNumber(r.magnets.bug_magnets ?? 0)}
                {(r.magnets.top_bug_magnets ?? []).slice(0, 3).map((m) => (
                  <span key={m.file} className="mt-0.5 block text-right font-mono text-[10px] text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
                    {m.file}
                  </span>
                ))}
              </span>
            ) : (
              dash
            ),
            h?.taken_at ? (
              <span key="s">
                {formatDate(h.taken_at)}
                {staleSnapshot ? (
                  <span className="mt-0.5 block text-[10px] text-[var(--color-warning)]">{t("snapshotBehind")}</span>
                ) : null}
              </span>
            ) : (
              dash
            ),
          ],
        };
      })}
      note={t("notMeasuredNote")}
    />
  );
}

function SeamsTable({ seams, t }: { seams: EstateSeam[]; t: Translator }) {
  if (seams.length === 0) return null;
  const shown = seams.slice(0, SEAM_ROWS);
  return (
    <EstateTable
      caption={t("check.seams")}
      columns={[
        { label: t("col.pair") },
        { label: t("col.edge") },
        { label: t("col.confidence"), numeric: true },
        { label: t("col.reach"), numeric: true },
        { label: t("col.coChange") },
        { label: t("col.seamScore"), numeric: true },
      ]}
      rows={shown.map((s) => ({
        key: `${s.services[0]}${s.direction}${s.services[1]}`,
        cells: [
          <span key="p" className="text-[var(--color-text-primary)]">
            {s.services[0]} <span className={`font-mono ${quiet}`}>{s.direction}</span> {s.services[1]}
          </span>,
          s.kinds.length > 0 ? <span key="k" className="font-mono text-[11px]">{s.kinds.join("+")}</span> : <span key="k" className={quiet}>{t("coChangeOnly")}</span>,
          s.confidence == null ? "—" : s.confidence.toFixed(2),
          formatNumber(s.reach),
          s.declared
            ? t("behindContract")
            : s.co_change == null
              ? <span key="c" className={quiet}>{t("notMeasured")}</span>
              : s.co_change > 0
                ? t("coChangeUnexplained", { value: s.co_change.toFixed(2) })
                : t("none"),
          s.score == null ? "—" : s.score_is_floor ? t("scoreFloor", { score: s.score }) : String(s.score),
        ],
      }))}
      note={shownOf(t, shown.length, seams.length)}
    />
  );
}

function StartServicesTable({ reach, t }: { reach: Record<string, number>; t: Translator }) {
  const ranked = Object.entries(reach)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (ranked.length === 0) return null;
  const shown = ranked.slice(0, START_SERVICE_ROWS);
  return (
    <EstateTable
      caption={t("check.start-services")}
      columns={[{ label: t("col.service") }, { label: t("col.reaches"), numeric: true }]}
      rows={shown.map(([service, n]) => ({
        key: service,
        cells: [
          <span key="s" className="font-medium text-[var(--color-text-primary)]">{service}</span>,
          formatNumber(n),
        ],
      }))}
      note={shownOf(t, shown.length, ranked.length)}
    />
  );
}

function StartFilesTable({ repos, t }: { repos: Record<string, EstateStartFile[]>; t: Translator }) {
  // Repos keep the engine's order: widest reach first. Three files each, as the CLI lists them.
  const rows = Object.entries(repos).flatMap(([repo, files]) =>
    files.slice(0, 3).map((f, i) => ({ repo, file: f, first: i === 0 })),
  );
  if (rows.length === 0) return null;
  return (
    <EstateTable
      caption={t("check.start-files")}
      columns={[{ label: t("col.repo") }, { label: t("col.file") }, { label: t("col.docs") }]}
      rows={rows.map(({ repo, file, first }) => ({
        key: `${repo}:${file.path}`,
        cells: [
          first ? <span key="r" className="font-medium text-[var(--color-text-primary)]">{repo}</span> : "",
          <span key="f" className={mono}>{file.path}</span>,
          file.doc_words > 0 ? t("docWords", { count: file.doc_words }) : <span key="d" className={quiet}>{t("undocumented")}</span>,
        ],
      }))}
    />
  );
}
