"use client";

/**
 * One contract, as the shared right-hand panel.
 *
 * Built on `AdaptivePanel` like every other drawer in the app, so it slides in
 * from the right on desktop and becomes a bottom sheet on a phone. It answers
 * what the contract is, where it lives, how extraction found it, what sits on
 * the other side (or why nothing does and what to do about it), what changed,
 * and which tests guard it. The detail route stays the shareable identity, so
 * "Open full page" is always offered.
 *
 * Presentation only. The host fetches the detail and the test impact when the
 * panel opens, and supplies how it routes to code.
 */

import type { ReactNode } from "react";
import { ExternalLink } from "lucide-react";
import type {
  BreakingChange,
  ContractSchema,
  SchemaField,
  WorkspaceContractLinkEntry,
  WorkspaceTestImpactResponse,
} from "@repowise-dev/types/workspace";

import { AdaptivePanel } from "../shared/adaptive-panel";
import {
  BreakingChangeRow,
  breakingChangeKey,
  sortChangesBySeverity,
  type BreakingChangeLinks,
} from "./breaking-change-row";
import { ContractIdentity, FilePathText, distinctService } from "./contract-identity";
import { ContractPromptButton } from "./contract-prompt-button";
import { ContractTestsSection } from "./contract-tests-section";
import { contractTypeLabel } from "./contract-type-label";
import {
  contractLede,
  contractMetaEntries,
  contractMetaLabel,
  flattenSchemaFields,
  providerLinkedProse,
  schemaFieldConstraints,
  unlinkedProviderProse,
  unmatchedConsumerProse,
  unmatchedReasonCopy,
  type ContractEntry,
} from "./contract-facts";

/** The identity a contract is fetched and linked by. */
export interface ContractRef {
  repo: string;
  file_path: string;
  contract_id: string;
}

export interface ContractDrawerProps {
  /** The contract, once known. Null while a deep link is still loading. */
  contract: ContractEntry | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Title shown while `contract` is null, usually the requested id. */
  pendingId?: string | undefined;
  /** The detail answer is still loading; links and reason are not known yet. */
  loading?: boolean | undefined;
  /** The detail could not be read, worded by the host. */
  error?: string | null | undefined;
  /** The matched links this contract sits on. */
  links?: WorkspaceContractLinkEntry[];
  /** Why a consumer matched nothing, from the detail endpoint. */
  unmatchedReason?: string | null | undefined;
  /** Request and response fields, when a reader recovered them. */
  schema?: ContractSchema | null | undefined;
  /** Changes from the latest report that touch this contract id. */
  breakingChanges?: BreakingChange[];
  /** Consumer tests for a provider. Null when the host did not ask. */
  testImpact?: WorkspaceTestImpactResponse | null | undefined;
  testImpactLoading?: boolean | undefined;
  testImpactError?: string | null | undefined;
  /** How the host routes to code. A null return renders plain text. */
  codeLinks?: BreakingChangeLinks | undefined;
  /** The detail route for this contract. */
  fullPageHref?: string | undefined;
  /** Swap the panel to the other side of a link. */
  onSelectContract?: ((ref: ContractRef) => void) | undefined;
}

export function ContractDrawer({
  contract,
  open,
  onOpenChange,
  pendingId,
  loading = false,
  error = null,
  links = [],
  unmatchedReason = null,
  schema,
  breakingChanges = [],
  testImpact = null,
  testImpactLoading = false,
  testImpactError = null,
  codeLinks,
  fullPageHref,
  onSelectContract,
}: ContractDrawerProps) {
  const id = contract?.contract_id ?? pendingId ?? "";

  return (
    <AdaptivePanel
      open={open}
      onOpenChange={onOpenChange}
      eyebrow={
        contract
          ? `${contractTypeLabel(contract.contract_type)} contract / ${
              contract.role === "provider" ? "Provider" : "Consumer"
            }`
          : "Contract"
      }
      title={
        // The panel's title style breaks anywhere; the identity opts back out
        // so a path wraps at its slashes.
        id ? <ContractIdentity contractId={id} size="title" /> : "Contract"
      }
      widthClassName="md:max-w-[600px]"
    >
      {contract ? (
        <Body
          contract={contract}
          loading={loading}
          error={error}
          links={links}
          unmatchedReason={unmatchedReason}
          schema={schema ?? null}
          breakingChanges={breakingChanges}
          testImpact={testImpact}
          testImpactLoading={testImpactLoading}
          testImpactError={testImpactError}
          codeLinks={codeLinks}
          fullPageHref={fullPageHref}
          onSelectContract={onSelectContract}
        />
      ) : (
        <p className="px-4 py-4 text-xs text-[var(--color-text-tertiary)]">
          {error ?? (loading ? "Loading contract..." : "This contract is not in the workspace.")}
        </p>
      )}

    </AdaptivePanel>
  );
}

function Body({
  contract,
  loading,
  error,
  links,
  unmatchedReason,
  schema,
  breakingChanges,
  testImpact,
  testImpactLoading,
  testImpactError,
  codeLinks,
  fullPageHref,
  onSelectContract,
}: {
  contract: ContractEntry;
  loading: boolean;
  error: string | null;
  links: WorkspaceContractLinkEntry[];
  unmatchedReason: string | null;
  schema: ContractSchema | null;
  breakingChanges: BreakingChange[];
  testImpact: WorkspaceTestImpactResponse | null;
  testImpactLoading: boolean;
  testImpactError: string | null;
  codeLinks?: BreakingChangeLinks | undefined;
  fullPageHref?: string | undefined;
  onSelectContract?: ((ref: ContractRef) => void) | undefined;
}) {
  const isProvider = contract.role === "provider";
  const fileHref = codeLinks?.fileHref?.(contract.repo, contract.file_path) ?? null;
  const symbolHref = contract.symbol_id
    ? (codeLinks?.symbolHref?.(contract.repo, contract.symbol_id) ?? null)
    : null;
  const metaEntries = contractMetaEntries(contract.meta);

  return (
    <>
      <div className="flex min-h-0 flex-1 flex-col gap-6 px-4 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <p className="min-w-0 flex-1 text-[15px] leading-relaxed text-[var(--color-text-secondary)]">
            {contractLede(contract)}
          </p>
          <ContractPromptButton
            contract={contract}
            links={links}
            unmatchedReason={unmatchedReason}
            breakingChanges={breakingChanges}
          />
        </div>

        <Section title="Where it lives">
          <Facts>
            <Fact label="Repository">
              {contract.repo}
              {contract.service ? (
                <span className="text-[var(--color-text-tertiary)]">
                  {" "}
                  / <span className="font-mono text-xs">{contract.service}</span>
                </span>
              ) : null}
            </Fact>
            <Fact label="File">
              {fileHref ? (
                <a
                  href={fileHref}
                  className="rounded-sm hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
                >
                  <FilePathText path={contract.file_path} line={contract.line} accent />
                </a>
              ) : (
                <FilePathText path={contract.file_path} line={contract.line} />
              )}
            </Fact>
            {contract.symbol_name ? (
              <Fact label="Symbol">
                {symbolHref ? (
                  <a
                    href={symbolHref}
                    className="font-mono text-xs text-[var(--color-accent-primary)] [overflow-wrap:anywhere] hover:underline"
                  >
                    {contract.symbol_name}
                  </a>
                ) : (
                  <Mono>{contract.symbol_name}</Mono>
                )}
              </Fact>
            ) : null}
            <Fact label="Contract id">
              <Mono>{contract.contract_id}</Mono>
            </Fact>
          </Facts>
        </Section>

        <Counterparts
          contract={contract}
          loading={loading}
          error={error}
          links={links}
          unmatchedReason={unmatchedReason}
          codeLinks={codeLinks}
          onSelectContract={onSelectContract}
        />

        {breakingChanges.length > 0 ? (
          <Section title="Breaking changes">
            <div className="overflow-hidden rounded-md border border-[var(--color-border-default)] text-xs text-[var(--color-text-secondary)]">
              {sortChangesBySeverity(breakingChanges).map((change) => (
                <BreakingChangeRow
                  key={breakingChangeKey(change)}
                  change={change}
                  {...(codeLinks ? { links: codeLinks } : {})}
                />
              ))}
            </div>
          </Section>
        ) : null}

        {isProvider ? (
          testImpactLoading ? (
            <Section title="Tests to run">
              <Quiet>Finding the tests in the calling repositories...</Quiet>
            </Section>
          ) : (
            <ContractTestsSection
              result={testImpact}
              contractId={contract.contract_id}
              error={testImpactError}
              compact
            />
          )
        ) : null}

        {schema && (schema.request_fields.length > 0 || schema.response_fields.length > 0) ? (
          <Section title="Shape">
            <Quiet>
              Recovered by the {schema.source} reader. Fields are what the declaration names, not
              what any one caller passes.
            </Quiet>
            {schema.request_fields.length > 0 ? (
              <FieldList caption="Request" fields={schema.request_fields} />
            ) : null}
            {schema.response_fields.length > 0 ? (
              <FieldList caption="Response" fields={schema.response_fields} />
            ) : null}
          </Section>
        ) : null}

        <Section title="How it was found">
          <Facts>
            <Fact label="Confidence">
              <span className="font-mono text-xs tabular-nums">
                {Math.round(contract.confidence * 100)}%
              </span>
            </Fact>
            {metaEntries.map(([key, value]) => (
              <Fact key={key} label={contractMetaLabel(key)}>
                <Mono>{value}</Mono>
              </Fact>
            ))}
          </Facts>
          {contract.meta?.extraction_layer === "regex" ? (
            <Quiet>
              Found by a text pattern rather than the parsed symbol table, which is where recall is
              least certain.
            </Quiet>
          ) : null}
        </Section>
      </div>

      {fullPageHref ? (
        <div className="sticky bottom-0 border-t border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-4 py-3">
          <a
            href={fullPageHref}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-[var(--color-accent-primary)] hover:underline"
          >
            <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            Open full page
          </a>
        </div>
      ) : null}
    </>
  );
}

/** The other side of the contract, or why there is none and what to do next. */
function Counterparts({
  contract,
  loading,
  error,
  links,
  unmatchedReason,
  codeLinks,
  onSelectContract,
}: {
  contract: ContractEntry;
  loading: boolean;
  error: string | null;
  links: WorkspaceContractLinkEntry[];
  unmatchedReason: string | null;
  codeLinks?: BreakingChangeLinks | undefined;
  onSelectContract?: ((ref: ContractRef) => void) | undefined;
}) {
  const isProvider = contract.role === "provider";
  const title = isProvider ? "Callers" : "Served by";

  if (loading) {
    return (
      <Section title={title}>
        <Quiet>Loading the links for this contract...</Quiet>
      </Section>
    );
  }
  if (error) {
    return (
      <Section title={title}>
        <Quiet>{error}</Quiet>
      </Section>
    );
  }
  if (links.length === 0) {
    return (
      <Section title={isProvider ? "No caller found" : unmatchedReasonCopy(unmatchedReason).title}>
        <Quiet>
          {isProvider
            ? unlinkedProviderProse(contract)
            : unmatchedConsumerProse(unmatchedReason, contract)}
        </Quiet>
      </Section>
    );
  }

  return (
    <Section title={title}>
      <Quiet>
        {isProvider
          ? providerLinkedProse(links, contract.repo)
          : "This call resolves to the code that serves it. A match joins a call site to a declaration; it does not mean both were written against a shared schema."}
      </Quiet>
      <ul className="m-0 flex list-none flex-col p-0">
        {links.map((link) => (
          <CounterpartRow
            key={`${link.consumer_contract_id ?? ""}|${link.provider_repo}|${link.provider_file}|${link.consumer_repo}|${link.consumer_file}`}
            link={link}
            side={isProvider ? "consumer" : "provider"}
            codeLinks={codeLinks}
            onSelectContract={onSelectContract}
          />
        ))}
      </ul>
    </Section>
  );
}

/**
 * One counterpart. The row swaps the panel to that contract, which is the one
 * verb; the code link is a separate, smaller target beside it.
 */
function CounterpartRow({
  link,
  side,
  codeLinks,
  onSelectContract,
}: {
  link: WorkspaceContractLinkEntry;
  side: "provider" | "consumer";
  codeLinks?: BreakingChangeLinks | undefined;
  onSelectContract?: ((ref: ContractRef) => void) | undefined;
}) {
  const repo = side === "provider" ? link.provider_repo : link.consumer_repo;
  const file = side === "provider" ? link.provider_file : link.consumer_file;
  const symbol = side === "provider" ? link.provider_symbol : link.consumer_symbol;
  const service = distinctService(
    side === "provider" ? link.provider_service : link.consumer_service,
    side === "provider" ? link.provider_file : link.consumer_file,
  );
  const symbolId = side === "provider" ? link.provider_symbol_id : link.consumer_symbol_id;
  // A consumer bound through another name (a queue on an exchange) keeps its own id.
  const id = side === "consumer" ? (link.consumer_contract_id ?? link.contract_id) : link.contract_id;
  const codeHref =
    (symbolId ? codeLinks?.symbolHref?.(repo, symbolId) : null) ??
    codeLinks?.fileHref?.(repo, file) ??
    null;

  const content = (
    <>
      <span className="text-xs font-medium text-[var(--color-text-primary)]">
        {repo}
        {service ? (
          <span className="font-mono font-normal text-[var(--color-text-tertiary)]"> / {service}</span>
        ) : null}
      </span>
      <span className="block">
        <FilePathText path={file} />
      </span>
      {symbol ? (
        <span className="block font-mono text-[10px] text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
          {symbol}
        </span>
      ) : null}
      <span className="block text-[10px] text-[var(--color-text-tertiary)]">
        {link.match_type} match,{" "}
        <span className="font-mono tabular-nums">{Math.round(link.confidence * 100)}%</span>
      </span>
    </>
  );

  return (
    <li className="flex items-start gap-2 border-t border-[var(--color-border-default)] py-2 first:border-t-0">
      {onSelectContract ? (
        <button
          type="button"
          onClick={() => onSelectContract({ repo, file_path: file, contract_id: id })}
          className="-mx-1 min-w-0 flex-1 rounded-md px-1 py-0.5 text-left hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
          aria-label={`Open the ${side} in ${repo}, ${file}`}
        >
          {content}
        </button>
      ) : (
        <div className="min-w-0 flex-1">{content}</div>
      )}
      {codeHref ? (
        <a
          href={codeHref}
          className="shrink-0 pt-0.5 text-[10px] font-medium text-[var(--color-accent-primary)] hover:underline"
        >
          Code
        </a>
      ) : null}
    </li>
  );
}

function FieldList({ caption, fields }: { caption: string; fields: SchemaField[] }) {
  const rows = flattenSchemaFields(fields);
  return (
    <div>
      <div className="mb-1 text-xs font-medium text-[var(--color-text-secondary)]">{caption}</div>
      <ul className="flex flex-col">
        {rows.map(({ field: f, path }, i) => (
          <li
            key={`${path}|${f.number ?? i}`}
            className="flex flex-wrap items-baseline gap-x-2 border-t border-[var(--color-border-default)] py-1"
          >
            <span className="font-mono text-xs text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
              {path}
            </span>
            <span className="font-mono text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
              {f.type}
            </span>
            <span className="text-[10px] text-[var(--color-text-tertiary)]">
              {schemaFieldConstraints(f)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h4 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        {title}
      </h4>
      {children}
    </section>
  );
}

function Quiet({ children }: { children: ReactNode }) {
  return (
    <p className="text-xs leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
      {children}
    </p>
  );
}

function Facts({ children }: { children: ReactNode }) {
  return (
    <dl className="m-0 grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-[max-content_minmax(0,1fr)]">
      {children}
    </dl>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="contents">
      <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)] sm:pt-0.5">
        {label}
      </dt>
      {/* min-w-0 or a long path refuses to shrink and pushes the panel wide. */}
      <dd className="m-0 min-w-0 text-xs text-[var(--color-text-secondary)]">{children}</dd>
    </div>
  );
}

function Mono({ children }: { children: ReactNode }) {
  return <span className="font-mono text-xs [overflow-wrap:anywhere]">{children}</span>;
}
