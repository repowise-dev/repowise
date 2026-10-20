import type { ReactNode } from "react";
import Link from "next/link";
import { getTranslations } from "next-intl/server";
import type {
  WorkspaceContractDetail,
  WorkspaceContractEntry,
  WorkspaceContractLinkEntry,
} from "@/lib/api/types";
import type {
  ContractSchema,
  SchemaField,
  WorkspaceTestImpactResponse,
} from "@repowise-dev/types/workspace";
import { contractTypeLabel } from "@repowise-dev/ui/workspace/contract-type-label";
import { ContractTestsSection } from "@repowise-dev/ui/workspace/contract-tests-section";
import {
  asContractSchema,
  contractHeading,
  contractLede,
  contractMetaEntries,
  contractMetaLabel,
  contractMetaString,
  flattenSchemaFields,
  schemaFieldConstraints,
} from "@repowise-dev/ui/workspace/contract-facts";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { ContractPromptButton } from "@repowise-dev/ui/workspace/contract-prompt-button";

/**
 * One contract, read top to bottom.
 *
 * A reading surface rather than a chrome one, so it takes the reading type
 * scale. That is also why the sections are written here rather than composed
 * from `OverviewSection`: that component's heading sits at 16px, which is the
 * reading body size, and a heading the same size as the text under it is a
 * bolded paragraph.
 *
 * The prose is the part doing the work. Most providers in a workspace this
 * size have no caller, which is the ordinary condition of an exported symbol
 * rather than a finding, and no amount of layout says that. Nothing on this
 * page carries a health band, so nothing on it is green, amber or red.
 */

interface Props {
  detail: WorkspaceContractDetail;
  /** Repo alias to indexed repo id. A never-indexed repo has no entry. */
  repoIds: Record<string, string>;
  /** Consumer tests guarding this contract. Providers only; null otherwise. */
  testImpact?: WorkspaceTestImpactResponse | null;
  testImpactError?: string | null;
}

export async function ContractBody({ detail, repoIds, testImpact, testImpactError }: Props) {
  const t = await getTranslations("contracts");
  const { contract, links, unmatched_reason: unmatchedReason } = detail;
  const isProvider = contract.role === "provider";
  const schema = asContractSchema(detail.contract_schema);

  return (
    <div className="mx-auto w-full max-w-[1280px] p-[var(--page-pad)]">
      <Link
        href="/workspace/contracts"
        className="text-xs font-medium text-[var(--color-accent-primary)] hover:underline"
      >
        <span aria-hidden>&larr;</span> {t("detail.back")}
      </Link>

      <header className="mt-6 flex flex-col gap-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          {contractTypeLabel(contract.contract_type)} {t("detail.typeContract")}
          <span className="mx-2 text-[var(--color-border-hover)]">/</span>
          {isProvider ? t("detail.roleProvider") : t("detail.roleConsumer")}
        </p>
        <h1 className="text-[2rem] font-semibold leading-tight tracking-tight text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
          {contractHeading(contract)}
        </h1>
        <p className="max-w-[68ch] text-base leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
          {contractLede(contract)}
        </p>
        <div>
          <ContractPromptButton
            contract={contract}
            links={links}
            unmatchedReason={unmatchedReason}
          />
        </div>
      </header>

      <Section
        title={isProvider ? t("detail.declaredHere") : t("detail.calledHere")}
        description={
          isProvider
            ? t("detail.declaredHereDescription")
            : t("detail.calledHereDescription")
        }
      >
        <Facts>
          <Fact label={t("detail.fact.repository")}>{contract.repo}</Fact>
          <Fact label={t("detail.fact.file")}>
            <FileRef
              repo={contract.repo}
              path={contract.file_path}
              line={contract.line}
              repoIds={repoIds}
            />
          </Fact>
          {contract.service && (
            <Fact label={t("detail.fact.service")}>{contract.service}</Fact>
          )}
          <Fact label={t("detail.fact.symbol")}>
            {/* The extractor's own string, dialect prefix included: it is what
                names the framework that matched, and shortening it would hide
                the difference between a route declaration and a client call. */}
            <span className="font-mono text-xs [overflow-wrap:anywhere]">
              {contract.symbol_name || t("detail.symbolNone")}
            </span>
          </Fact>
          <Fact label={t("detail.fact.confidence")}>
            <span className="tabular-nums">{Math.round(contract.confidence * 100)}%</span>
          </Fact>
          <Fact label={t("detail.fact.contractId")}>
            <span className="font-mono text-xs [overflow-wrap:anywhere]">
              {contract.contract_id}
            </span>
          </Fact>
        </Facts>
      </Section>

      <LinkSection
        contract={contract}
        links={links}
        unmatchedReason={unmatchedReason}
        repoIds={repoIds}
      />

      <ContractTestsSection
        result={testImpact ?? null}
        contractId={contract.contract_id}
        error={testImpactError ?? null}
      />

      <SchemaSection contract={contract} schema={schema} />

      <Section
        title={t("detail.howFound")}
        description={t("detail.howFoundDescription")}
      >
        <Facts>
          {contractMetaEntries(contract.meta).map(([key, value]) => (
            <Fact key={key} label={contractMetaLabel(key)}>
              <span className="font-mono text-xs [overflow-wrap:anywhere]">{value}</span>
            </Fact>
          ))}
          {contractMetaEntries(contract.meta).length === 0 && (
            <Fact label={t("detail.fact.detail")}>
              <span className="text-[var(--color-text-tertiary)]">
                {t("detail.extractorNone")}
              </span>
            </Fact>
          )}
        </Facts>
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The link section, and the states a contract can be in
// ---------------------------------------------------------------------------

/** The minimal translator shape the prose helpers below need; next-intl's `t` fits. */
type Translator = (key: string, values?: Record<string, string | number>) => string;

/**
 * The unmatched-reason titles.
 *
 * The prose helpers in `@repowise-dev/ui` carry this copy for the browser and
 * stay language-agnostic, so the label is named on this side instead of read
 * out of the shared package.
 */
const REASON_TITLE_KEYS: Record<string, string> = {
  no_provider: "detail.reasonTitle.no_provider",
  unlinked: "detail.reasonTitle.unlinked",
  internal_only: "detail.reasonTitle.internal_only",
  external_host: "detail.reasonTitle.external_host",
};

/**
 * A provider nothing calls, which is the ordinary state of exported code.
 *
 * The sentence is composed here rather than in `packages/ui` so it follows the
 * page's locale; the excluded set is what the matcher leaves out by design.
 */
function unmatchedProviderDescription(
  t: Translator,
  contract: WorkspaceContractEntry,
): string {
  return contract.service
    ? t("detail.prose.unlinkedProviderWithService", {
        repo: contract.repo,
        service: contract.service,
      })
    : t("detail.prose.unlinkedProvider", { repo: contract.repo });
}

/** One consumer's unmatched state as a paragraph, with the concrete next step. */
function unmatchedConsumerDescription(
  t: Translator,
  reason: string | null,
  contract: WorkspaceContractEntry,
): string {
  const host = contractMetaString(contract.meta, "host");
  switch (reason) {
    case "external_host":
      return host
        ? t("detail.prose.unmatchedExternalHost", { host })
        : t("detail.prose.unmatchedExternalHostGeneric");
    case "internal_only":
      return t("detail.prose.unmatchedInternalOnly");
    case "no_provider":
      return t("detail.prose.unmatchedNoProvider", {
        contract: contractHeading(contract),
      });
    case "unlinked":
      return t("detail.prose.unmatchedUnlinked");
    default:
      return t("detail.prose.unmatchedDefault");
  }
}

/** How many call sites resolve to a provider, and from where. */
function linkedProviderDescription(
  t: Translator,
  links: { consumer_repo: string }[],
  repo: string,
): string {
  const repos = new Set(links.map((l) => l.consumer_repo));
  if (repos.size === 1 && repos.has(repo)) {
    return links.length === 1
      ? t("detail.prose.linkedFromOne", { calls: links.length, repo })
      : t("detail.prose.linkedAllFromOne", { calls: links.length, repo });
  }
  return t("detail.prose.linkedAcross", {
    calls: links.length,
    repos: repos.size,
  });
}

async function LinkSection({
  contract,
  links,
  unmatchedReason,
  repoIds,
}: {
  contract: WorkspaceContractEntry;
  links: WorkspaceContractLinkEntry[];
  unmatchedReason: string | null;
  repoIds: Record<string, string>;
}) {
  const t = await getTranslations("contracts");
  const isProvider = contract.role === "provider";

  if (links.length === 0) {
    return (
      <Section
        title={
          isProvider
            ? t("detail.noCallerFound")
            : t(REASON_TITLE_KEYS[unmatchedReason ?? ""] ?? "detail.reasonTitle.unknown")
        }
        description={
          isProvider
            ? unmatchedProviderDescription(t, contract)
            : unmatchedConsumerDescription(t, unmatchedReason, contract)
        }
      />
    );
  }

  return (
    <Section
      title={isProvider ? t("detail.callers") : t("detail.servedBy")}
      description={
        isProvider
          ? linkedProviderDescription(t, links, contract.repo)
          : t("detail.servedByDescription")
      }
    >
      <LinkTable links={links} side={isProvider ? "consumer" : "provider"} repoIds={repoIds} />
    </Section>
  );
}

async function LinkTable({
  links,
  side,
  repoIds,
}: {
  links: WorkspaceContractLinkEntry[];
  side: "provider" | "consumer";
  repoIds: Record<string, string>;
}) {
  const t = await getTranslations("contracts");
  // A local table rather than `ContractLinksTable`: that one leads with the
  // contract id and its type, and on this page both of those are the heading.
  // What is left to say is the other side of each link.
  return (
    <TableScroll>
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">
          {side === "provider"
            ? t("detail.caption.providersServing")
            : t("detail.caption.callSites")}
        </caption>
        <thead>
          <tr className="text-xs uppercase tracking-wider text-[var(--color-text-tertiary)]">
            <Th>{t("detail.fact.repository")}</Th>
            <Th>{t("detail.fact.file")}</Th>
            {/* Column priority, as the shared tables run it: below md only
                the repository and the path survive, because three wrapping
                mono columns in 358px break a path mid-token to make room for
                a symbol nobody came here to read. */}
            <Th className="max-md:hidden">{t("detail.fact.symbol")}</Th>
            <Th className="max-lg:hidden">{t("detail.col.match")}</Th>
            <Th className="max-md:hidden">{t("detail.fact.confidence")}</Th>
          </tr>
        </thead>
        <tbody>
          {links.map((link) => {
            const repo = side === "provider" ? link.provider_repo : link.consumer_repo;
            const file = side === "provider" ? link.provider_file : link.consumer_file;
            const symbol = side === "provider" ? link.provider_symbol : link.consumer_symbol;
            const service = side === "provider" ? link.provider_service : link.consumer_service;
            return (
              <tr
                key={`${repo}|${file}|${symbol}`}
                className="border-t border-[var(--color-border-default)] align-top"
              >
                <Td>
                  <span className="text-xs font-medium text-[var(--color-text-primary)]">
                    {repo}
                  </span>
                  {service && (
                    <span className="mt-0.5 block font-mono text-[11px] text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
                      {service}
                    </span>
                  )}
                </Td>
                <Td>
                  <FileRef repo={repo} path={file} line={null} repoIds={repoIds} />
                </Td>
                <Td className="max-md:hidden">
                  <span className="font-mono text-xs text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
                    {symbol}
                  </span>
                </Td>
                <Td className="max-lg:hidden">
                  <span className="text-xs text-[var(--color-text-secondary)]">
                    {link.match_type}
                  </span>
                </Td>
                <Td className="max-lg:hidden">
                  <span className="text-xs tabular-nums text-[var(--color-text-tertiary)]">
                    {Math.round(link.confidence * 100)}%
                  </span>
                </Td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableScroll>
  );
}

// ---------------------------------------------------------------------------
// The shape section
// ---------------------------------------------------------------------------

/**
 * The request and response shapes, when a parser recovered them.
 *
 * The view adapts and then says what it dropped. A `data` contract never
 * carries a shape at all, and the signature reader recovers parameters and
 * stops, so a response table would be an empty box under every contract that
 * has one. Naming what would fill it is the whole content of the absent case.
 */
async function SchemaSection({
  contract,
  schema,
}: {
  contract: WorkspaceContractEntry;
  schema: ContractSchema | null;
}) {
  const t = await getTranslations("contracts");
  if (!schema) {
    return <Section title={t("detail.shape")} description={noSchemaProse(t, contract.contract_type)} />;
  }

  const hasRequest = schema.request_fields.length > 0;
  const hasResponse = schema.response_fields.length > 0;

  return (
    <Section title={t("detail.shape")} description={schemaProse(t, schema.source, hasResponse)}>
      {hasRequest && (
        <FieldTable caption={t("detail.field.request")} fields={schema.request_fields} />
      )}
      {hasResponse && (
        <FieldTable caption={t("detail.field.response")} fields={schema.response_fields} />
      )}
      {!hasRequest && !hasResponse && (
        <p className="text-sm text-[var(--color-text-tertiary)]">
          {t("detail.shapeEmptyFields")}
        </p>
      )}
    </Section>
  );
}

function schemaProse(t: Translator, source: string, hasResponse: boolean): string {
  const head = t("detail.schema.recovered", { source });
  if (hasResponse) {
    return t("detail.schema.withResponse", { head });
  }
  if (source === "signature") {
    return t("detail.schema.signature", { head });
  }
  return t("detail.schema.noReturn", { head });
}

function noSchemaProse(t: Translator, type: string): string {
  if (type === "data") {
    return t("detail.schema.dataTable");
  }
  return t("detail.schema.noReader");
}

async function FieldTable({ caption, fields }: { caption: string; fields: SchemaField[] }) {
  const t = await getTranslations("contracts");
  const rows = flattenSchemaFields(fields);
  return (
    <TableScroll>
      <table className="w-full border-collapse text-left">
        <caption className="pb-2 text-left text-xs font-medium text-[var(--color-text-secondary)]">
          {caption}
        </caption>
        <thead>
          <tr className="text-xs uppercase tracking-wider text-[var(--color-text-tertiary)]">
            <Th>{t("detail.field.field")}</Th>
            <Th>{t("detail.field.type")}</Th>
            <Th>{t("detail.field.required")}</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ field: f, path }, i) => (
            <tr
              key={`${path}|${f.number ?? i}`}
              className="border-t border-[var(--color-border-default)]"
            >
              <Td>
                <span className="font-mono text-xs text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
                  {path}
                </span>
              </Td>
              <Td>
                <span className="font-mono text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
                  {f.type}
                </span>
              </Td>
              <Td>
                <span className="text-xs text-[var(--color-text-tertiary)]">
                  {schemaFieldConstraints(f)}
                </span>
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

// ---------------------------------------------------------------------------
// Pieces
// ---------------------------------------------------------------------------

/**
 * A hairline and vertical rhythm, not a card. Five states invite five boxes,
 * and boxes at the same weight say every section matters equally.
 */
function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children?: ReactNode;
}) {
  return (
    <section className="mt-10 flex flex-col gap-3 border-t border-[var(--color-border-default)] pt-6 sm:mt-12 sm:pt-8">
      <h2 className="text-xl font-semibold tracking-tight text-[var(--color-text-primary)]">
        {title}
      </h2>
      {description && (
        <p className="max-w-[68ch] text-base leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
          {description}
        </p>
      )}
      {children}
    </section>
  );
}

/** Wide content scrolls inside its own container, and bleeds to the edge on
 *  mobile rather than sitting in a padded box narrower than the screen. */
function TableScroll({ children }: { children: ReactNode }) {
  return (
    <div className="-mx-[var(--page-pad)] overflow-x-auto px-[var(--page-pad)] sm:mx-0 sm:px-0">
      {children}
    </div>
  );
}

function Facts({ children }: { children: ReactNode }) {
  return (
    <dl className="m-0 grid grid-cols-1 gap-x-10 gap-y-4 sm:grid-cols-[max-content_minmax(0,1fr)]">
      {children}
    </dl>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="contents">
      <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)] sm:pt-1">
        {label}
      </dt>
      {/* min-w-0 or a long path refuses to shrink and pushes the page wide. */}
      <dd className="m-0 min-w-0 text-sm text-[var(--color-text-secondary)]">{children}</dd>
    </div>
  );
}

function Th({ children, className }: { children: ReactNode; className?: string }) {
  return <th className={`px-3 py-2 font-medium ${className ?? ""}`}>{children}</th>;
}

function Td({ children, className }: { children: ReactNode; className?: string }) {
  return <td className={`px-3 py-2 align-top ${className ?? ""}`}>{children}</td>;
}

/**
 * A path, linked into the repo's file page when that repo is indexed.
 *
 * A workspace repo that has never been indexed carries no `repo_id` and has no
 * page to send anybody to, so the path is printed rather than linked, matching
 * how the workspace listing degrades. The line is text rather than part of the
 * href because the file page takes no line parameter, and a link that lands
 * nowhere near the number it names is worse than the number on its own.
 */
async function FileRef({
  repo,
  path,
  line,
  repoIds,
}: {
  repo: string;
  path: string;
  line: number | null;
  repoIds: Record<string, string>;
}) {
  const t = await getTranslations("contracts");
  const repoId = repoIds[repo];
  const text = <span className="font-mono text-xs [overflow-wrap:anywhere]">{path}</span>;
  return (
    <span>
      {repoId ? (
        <Link
          href={fileEntityPath(`/repos/${repoId}`, path)}
          className="text-[var(--color-accent-primary)] hover:underline"
        >
          {text}
        </Link>
      ) : (
        <span className="text-[var(--color-text-tertiary)]">{text}</span>
      )}
      {line != null && (
        <span className="ml-2 text-xs tabular-nums text-[var(--color-text-tertiary)]">
          {t("detail.line", { line })}
        </span>
      )}
    </span>
  );
}
