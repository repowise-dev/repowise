import type { ReactNode } from "react";
import Link from "next/link";
import type {
  WorkspaceContractDetail,
  WorkspaceContractEntry,
  WorkspaceContractLinkEntry,
} from "@/lib/api/types";
import type { ContractSchema, SchemaField } from "@repowise-dev/types/workspace";
import { contractTypeLabel } from "@repowise-dev/ui/workspace/contract-type-badge";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { formatNumber } from "@repowise-dev/ui/lib/format";

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
}

export function ContractBody({ detail, repoIds }: Props) {
  const { contract, links, unmatched_reason: unmatchedReason } = detail;
  const isProvider = contract.role === "provider";
  const schema = asSchema(detail.contract_schema);

  return (
    <div className="mx-auto w-full max-w-[1280px] p-[var(--page-pad)]">
      <Link
        href="/workspace/contracts"
        className="text-xs font-medium text-[var(--color-accent-primary)] hover:underline"
      >
        <span aria-hidden>&larr;</span> Contracts
      </Link>

      <header className="mt-6 flex flex-col gap-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          {contractTypeLabel(contract.contract_type)} contract
          <span className="mx-2 text-[var(--color-border-hover)]">/</span>
          {isProvider ? "Provider" : "Consumer"}
        </p>
        <h1 className="text-[2rem] font-semibold leading-tight tracking-tight text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
          {headingFor(contract)}
        </h1>
        <p className="max-w-[68ch] text-base leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
          {ledeFor(contract)}
        </p>
      </header>

      <Section
        title={isProvider ? "Declared here" : "Called here"}
        description={
          isProvider
            ? "Where this contract is declared, and how confident extraction is that this is the declaration."
            : "The call site, and how confident extraction is that it names this contract."
        }
      >
        <Facts>
          <Fact label="Repository">{contract.repo}</Fact>
          <Fact label="File">
            <FileRef
              repo={contract.repo}
              path={contract.file_path}
              line={contract.line}
              repoIds={repoIds}
            />
          </Fact>
          {contract.service && <Fact label="Service">{contract.service}</Fact>}
          <Fact label="Symbol">
            {/* The extractor's own string, dialect prefix included: it is what
                names the framework that matched, and shortening it would hide
                the difference between a route declaration and a client call. */}
            <span className="font-mono text-xs [overflow-wrap:anywhere]">
              {contract.symbol_name || "—"}
            </span>
          </Fact>
          <Fact label="Confidence">
            <span className="tabular-nums">{Math.round(contract.confidence * 100)}%</span>
          </Fact>
          <Fact label="Contract id">
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

      <SchemaSection contract={contract} schema={schema} />

      <Section
        title="How it was found"
        description="What the extractor recorded about this contract. The layer is the part worth reading: an index contract came from the parsed symbol table, a regex one from a text dialect, which is where recall is least certain."
      >
        <Facts>
          {metaEntries(contract.meta).map(([key, value]) => (
            <Fact key={key} label={metaLabel(key)}>
              <span className="font-mono text-xs [overflow-wrap:anywhere]">{value}</span>
            </Fact>
          ))}
          {metaEntries(contract.meta).length === 0 && (
            <Fact label="Detail">
              <span className="text-[var(--color-text-tertiary)]">
                The extractor recorded none.
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

function LinkSection({
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
  const isProvider = contract.role === "provider";

  if (links.length === 0) {
    return (
      <Section
        title={isProvider ? "No caller found" : unmatchedTitle(unmatchedReason)}
        description={
          isProvider
            ? unlinkedProviderProse(contract)
            : unmatchedConsumerProse(unmatchedReason, contract)
        }
      />
    );
  }

  const otherRepos = new Set(links.map((l) => (isProvider ? l.consumer_repo : l.provider_repo)));
  const sameRepoOnly = otherRepos.size === 1 && otherRepos.has(contract.repo);

  return (
    <Section
      title={isProvider ? "Callers" : "Served by"}
      description={
        isProvider
          ? providerLinkedProse(links.length, otherRepos.size, sameRepoOnly, contract.repo)
          : "This call resolves to the code that serves it. A match joins a call site to a declaration; it does not mean the two were written against a shared schema."
      }
    >
      <LinkTable links={links} side={isProvider ? "consumer" : "provider"} repoIds={repoIds} />
    </Section>
  );
}

function providerLinkedProse(
  linkCount: number,
  repoCount: number,
  sameRepoOnly: boolean,
  repo: string,
): string {
  const one = linkCount === 1;
  const head = `${countPhrase(linkCount, "call site", "call sites")} resolve${one ? "s" : ""} to this contract`;
  if (sameRepoOnly) {
    // Every provider in this state on a real workspace has exactly one caller,
    // so the singular is the sentence that actually ships.
    return `${head}, ${one ? `and it is inside ${repo}` : `all of them inside ${repo}`}. A pair is skipped only when the repository and the service are both the same, so ${one ? "that is a call" : "these are calls"} made from a different service in the same repository.`;
  }
  return `${head} across ${countPhrase(repoCount, "repository", "repositories")}.`;
}

/**
 * The state most contracts on this workspace are in, and the one most likely
 * to be misread.
 *
 * It is the expected condition for a route or an exported symbol in a small
 * workspace, so it gets a sentence rather than a colour: an unlinked provider
 * carries no health band, and green, amber and red are reserved for readouts
 * that do. A reader who correctly inferred a colour here would be taught a
 * rule that makes them wrong about the next mark they see.
 */
function unlinkedProviderProse(contract: WorkspaceContractEntry): string {
  // A pair is skipped when the repository *and* the service both match, so the
  // excluded set is not "everything in this repo" unless this declaration sits
  // outside a service too. Naming the wrong set here would send somebody
  // looking for a caller the page had told them could not exist.
  const excluded = contract.service
    ? `a call made from inside ${contract.repo}/${contract.service}`
    : `a call made from elsewhere in ${contract.repo} that also sits outside any service`;
  return `Nothing in this workspace resolves to this contract. Read that as two possibilities rather than one. A call is joined to the code that serves it only when the two do not share both a repository and a service, so ${excluded} is excluded by construction and never appears here. And a call written in a form extraction could not follow looks exactly the same from this side. Neither reading makes this dead code.`;
}

/**
 * The heading names the state, not the absence.
 *
 * "No provider found" is true of only two of these. A call to a third party
 * and a call that never leaves its own service both matched nothing on
 * purpose, and heading them as a failure to find something invites the reader
 * to go looking for it.
 */
function unmatchedTitle(reason: string | null): string {
  switch (reason) {
    case "external_host":
      return "Outside this workspace";
    case "internal_only":
      return "Not a cross-repo link";
    case "unlinked":
      return "No link formed";
    default:
      return "No provider found";
  }
}

function unmatchedConsumerProse(reason: string | null, contract: WorkspaceContractEntry): string {
  const host = typeof contract.meta?.host === "string" ? contract.meta.host : null;
  switch (reason) {
    case "external_host":
      return `This call goes to ${host ?? "a third-party host"}, which is not a service in this workspace. Calls to a literal external host are left out of matching on purpose, so there is nothing here to link it to and nothing to fix.`;
    case "internal_only":
      return `The only declarations matching this call live in the same repository and the same service as the call itself, so it never crosses a boundary. Intra-service calls are left out of the link set on purpose: a link is a claim that two services depend on each other.`;
    case "no_provider":
      return `Nothing in this workspace declares ${contract.contract_id}. Either it is served by something outside these repositories, or the declaration is written in a form extraction did not recognise.`;
    case "unlinked":
      return "A declaration with this id exists in another service, but no link was formed between the two. That is rare, and it points at a gap in the matcher rather than at the code.";
    default:
      return "This call matched no declaration, and no reason was recorded for it. Reasons come from the system graph, so a workspace that has not built one reports the count without the explanation.";
  }
}

function LinkTable({
  links,
  side,
  repoIds,
}: {
  links: WorkspaceContractLinkEntry[];
  side: "provider" | "consumer";
  repoIds: Record<string, string>;
}) {
  // A local table rather than `ContractLinksTable`: that one leads with the
  // contract id and its type, and on this page both of those are the heading.
  // What is left to say is the other side of each link.
  return (
    <TableScroll>
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">
          {side === "provider" ? "Providers serving this call" : "Call sites resolving here"}
        </caption>
        <thead>
          <tr className="text-xs uppercase tracking-wider text-[var(--color-text-tertiary)]">
            <Th>Repository</Th>
            <Th>File</Th>
            {/* Column priority, as the shared tables run it: below md only
                the repository and the path survive, because three wrapping
                mono columns in 358px break a path mid-token to make room for
                a symbol nobody came here to read. */}
            <Th className="max-md:hidden">Symbol</Th>
            <Th className="max-lg:hidden">Match</Th>
            <Th className="max-md:hidden">Confidence</Th>
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
function SchemaSection({
  contract,
  schema,
}: {
  contract: WorkspaceContractEntry;
  schema: ContractSchema | null;
}) {
  if (!schema) {
    return <Section title="Shape" description={noSchemaProse(contract.contract_type)} />;
  }

  const hasRequest = schema.request_fields.length > 0;
  const hasResponse = schema.response_fields.length > 0;

  return (
    <Section title="Shape" description={schemaProse(schema.source, hasResponse)}>
      {hasRequest && <FieldTable caption="Request" fields={schema.request_fields} />}
      {hasResponse && <FieldTable caption="Response" fields={schema.response_fields} />}
      {!hasRequest && !hasResponse && (
        <p className="text-sm text-[var(--color-text-tertiary)]">
          The reader produced a shape with no fields in it.
        </p>
      )}
    </Section>
  );
}

function schemaProse(source: string, hasResponse: boolean): string {
  const head = `Recovered by the ${source} reader.`;
  if (hasResponse) {
    return `${head} Fields are what the declaration names, not what any one caller passes.`;
  }
  if (source === "signature") {
    return `${head} It reads a declaration's parameters and stops there, so there is no return shape below; a response would have to come from an OpenAPI or proto declaration.`;
  }
  return `${head} It recovered no return shape for this contract.`;
}

function noSchemaProse(type: string): string {
  if (type === "data") {
    return "A table contract carries no field shape. It is matched on the table name, and nothing on that path reads a column list, so this section is empty for every table contract rather than for this one in particular.";
  }
  return "No reader recovered a shape for this contract. A shape comes off a typed declaration, so an untyped signature, a route declared through a decorator alone, or a file the index never parsed all arrive here without one.";
}

function FieldTable({ caption, fields }: { caption: string; fields: SchemaField[] }) {
  return (
    <TableScroll>
      <table className="w-full border-collapse text-left">
        <caption className="pb-2 text-left text-xs font-medium text-[var(--color-text-secondary)]">
          {caption}
        </caption>
        <thead>
          <tr className="text-xs uppercase tracking-wider text-[var(--color-text-tertiary)]">
            <Th>Field</Th>
            <Th>Type</Th>
            <Th>Required</Th>
          </tr>
        </thead>
        <tbody>
          {fields.map((f, i) => (
            <tr
              key={`${f.name}|${f.number ?? i}`}
              className="border-t border-[var(--color-border-default)]"
            >
              <Td>
                <span className="font-mono text-xs text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
                  {f.name}
                  {f.repeated && (
                    <span className="text-[var(--color-text-tertiary)]"> (repeated)</span>
                  )}
                </span>
              </Td>
              <Td>
                <span className="font-mono text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
                  {f.type}
                </span>
              </Td>
              <Td>
                <span className="text-xs text-[var(--color-text-tertiary)]">
                  {f.required ? "Required" : "Optional"}
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
function FileRef({
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
          line {line}
        </span>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Copy
// ---------------------------------------------------------------------------

/** The readable name of a contract, falling back to its id. */
export function headingFor(contract: WorkspaceContractEntry): string {
  const meta = contract.meta ?? {};
  const method = typeof meta.method === "string" ? meta.method : null;
  const path = typeof meta.path === "string" ? meta.path : null;
  const table = typeof meta.table === "string" ? meta.table : null;
  if (method && path) return `${method} ${path}`;
  if (table) return table;
  if (contract.contract_type === "code" && contract.symbol_name) return contract.symbol_name;
  return contract.contract_id;
}

/** One sentence saying what this record is, before any of the tables. */
function ledeFor(contract: WorkspaceContractEntry): string {
  const isProvider = contract.role === "provider";
  const pkg = typeof contract.meta?.package === "string" ? contract.meta.package : null;
  switch (contract.contract_type) {
    case "http":
      return isProvider
        ? `${contract.repo} serves this route.`
        : `${contract.repo} calls this route.`;
    case "data":
      return isProvider
        ? `${contract.repo} defines this table.`
        : `${contract.repo} reads or writes this table.`;
    case "code":
      return isProvider
        ? `${contract.repo} exports this from ${pkg ?? "a package"}.`
        : `${contract.repo} imports this from ${pkg ?? "a package"}.`;
    default:
      return isProvider
        ? `${contract.repo} declares this ${contractTypeLabel(contract.contract_type)} contract.`
        : `${contract.repo} consumes this ${contractTypeLabel(contract.contract_type)} contract.`;
  }
}

function countPhrase(n: number, one: string, many: string): string {
  return `${formatNumber(n)} ${n === 1 ? one : many}`;
}

/** `meta` keys in the order they read, across every contract type. */
const META_ORDER = [
  "extraction_layer",
  "framework",
  "client",
  "handler",
  "method",
  "path",
  "table",
  "verb",
  "package",
  "ecosystem",
  "host",
  "external",
  "base_token",
  "base_stripped",
];

const META_LABELS: Record<string, string> = {
  extraction_layer: "Layer",
  framework: "Framework",
  client: "Client",
  handler: "Handler",
  method: "Method",
  path: "Path",
  table: "Table",
  verb: "Verb",
  package: "Package",
  ecosystem: "Ecosystem",
  host: "Host",
  external: "External",
  base_token: "Base token",
  base_stripped: "Base stripped",
};

function metaLabel(key: string): string {
  return META_LABELS[key] ?? key.replace(/_/g, " ");
}

/**
 * `meta` as ordered, printable pairs. Keys vary by contract type and an
 * extractor is free to add one, so anything unrecognised is kept and printed
 * under its own name rather than dropped.
 */
function metaEntries(meta: Record<string, unknown>): [string, string][] {
  const known = new Set(META_ORDER);
  const present = (k: string) => meta?.[k] !== undefined && meta[k] !== null;
  const keys = [
    ...META_ORDER.filter(present),
    ...Object.keys(meta ?? {}).filter((k) => !known.has(k) && present(k)),
  ];
  return keys.map((k) => [k, String(meta[k])]);
}

/**
 * Narrow the loosely-typed `contract_schema` off the wire.
 *
 * It arrives as a bare object because the endpoint passes the artifact block
 * straight through, so the shape is checked here rather than assumed: a
 * workspace indexed by an older build can carry a block without the arrays.
 */
function asSchema(raw: Record<string, unknown> | null): ContractSchema | null {
  if (!raw) return null;
  return {
    source: typeof raw.source === "string" ? raw.source : "unknown",
    request_fields: Array.isArray(raw.request_fields) ? (raw.request_fields as SchemaField[]) : [],
    response_fields: Array.isArray(raw.response_fields)
      ? (raw.response_fields as SchemaField[])
      : [],
  };
}
