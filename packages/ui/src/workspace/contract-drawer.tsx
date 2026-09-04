"use client";

/**
 * One contract, as a right-hand drawer.
 *
 * The contracts table used to navigate away on a row click, which cost the
 * reader their filters and their scroll position to answer a question that
 * fits in a panel. The drawer answers it in place; the route it replaced is
 * still the shareable identity, so "Open full page" is always present and the
 * table keeps a modifier click on the contract id.
 *
 * Presentation only. It knows a contract names a repo, a file and a symbol,
 * but not how the host routes to code, so href builders come in the same shape
 * `BreakingChangeRow` takes them.
 */

import type { ReactNode } from "react";
import type {
  BreakingChange,
  ContractSchema,
  SchemaField,
  WorkspaceContractLinkEntry,
} from "@repowise-dev/types/workspace";

import { Sheet, SheetContent, SheetTitle } from "../ui/sheet";
import {
  BreakingChangeRow,
  breakingChangeKey,
  sortChangesBySeverity,
  type BreakingChangeLinks,
} from "./breaking-change-row";
import { contractTypeLabel } from "./contract-type-label";
import {
  contractHeading,
  contractLede,
  contractMetaEntries,
  contractMetaLabel,
  contractMetaString,
  type ContractEntry,
} from "./contract-facts";

/** `meta` keys the facts list already prints. */
const FACT_META_KEYS = new Set(["method", "path", "framework", "client"]);

export interface ContractDrawerProps {
  contract: ContractEntry | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The matched links this contract sits on. Use `linksForContract` to pick them. */
  links?: WorkspaceContractLinkEntry[];
  /** Request and response fields, when a reader recovered them. */
  schema?: ContractSchema | null | undefined;
  /** Changes from the latest report that touch this contract id. */
  breakingChanges?: BreakingChange[];
  /** How the host routes to code. A null return renders plain text. */
  codeLinks?: BreakingChangeLinks | undefined;
  /** The detail route for this contract. */
  fullPageHref?: string | undefined;
}

export function ContractDrawer({
  contract,
  open,
  onOpenChange,
  links = [],
  schema,
  breakingChanges = [],
  codeLinks,
  fullPageHref,
}: ContractDrawerProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        closeLabel="Close contract"
        // Wider than the nav sheet this primitive was written for: a request
        // path and a wrapping file path are what set the floor here.
        className="w-full max-w-[620px] sm:w-[92vw]"
      >
        {contract ? (
          <DrawerBody
            contract={contract}
            links={links}
            schema={schema ?? null}
            breakingChanges={breakingChanges}
            codeLinks={codeLinks}
            fullPageHref={fullPageHref}
          />
        ) : (
          <SheetTitle className="sr-only">Contract</SheetTitle>
        )}
      </SheetContent>
    </Sheet>
  );
}

function DrawerBody({
  contract,
  links,
  schema,
  breakingChanges,
  codeLinks,
  fullPageHref,
}: {
  contract: ContractEntry;
  links: WorkspaceContractLinkEntry[];
  schema: ContractSchema | null;
  breakingChanges: BreakingChange[];
  codeLinks?: BreakingChangeLinks | undefined;
  fullPageHref?: string | undefined;
}) {
  const isProvider = contract.role === "provider";
  const meta = contract.meta ?? {};
  const method = contractMetaString(meta, "method");
  const path = contractMetaString(meta, "path");
  const frameworkName = contractMetaString(meta, "framework");
  const client = contractMetaString(meta, "client");
  const fileHref = codeLinks?.fileHref?.(contract.repo, contract.file_path) ?? null;
  // What the facts above already print is dropped here, so the panel does not
  // say "fastapi" twice under two different headings.
  const metaEntries = contractMetaEntries(meta).filter(
    ([key]) => !FACT_META_KEYS.has(key),
  );

  return (
    <>
      <div className="border-b border-[var(--color-border-default)] px-5 py-4 pr-12">
        <div className="text-[11px] text-[var(--color-text-secondary)]">
          {contractTypeLabel(contract.contract_type)} contract
          <span className="mx-2 text-[var(--color-border-hover)]">/</span>
          {isProvider ? "Provider" : "Consumer"}
        </div>
        <SheetTitle className="mt-0.5 break-words font-mono text-[15px] font-semibold text-[var(--color-text-primary)]">
          {contractHeading(contract)}
        </SheetTitle>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--color-text-secondary)]">
          {contractLede(contract)}
        </p>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto px-5 py-5">
        <Facts>
          <Fact label="Contract id">
            <Mono>{contract.contract_id}</Mono>
          </Fact>
          {method ? (
            <Fact label="Method">
              <span className="text-xs">{method}</span>
            </Fact>
          ) : null}
          {path ? (
            <Fact label="Path">
              <Mono>{path}</Mono>
            </Fact>
          ) : null}
          <Fact label="Repository">
            <span className="text-xs">{contract.repo}</span>
          </Fact>
          <Fact label="File">
            {fileHref ? (
              <a href={fileHref} className="text-[var(--color-accent-primary)] hover:underline">
                <Mono>{contract.file_path}</Mono>
              </a>
            ) : (
              <Mono>{contract.file_path}</Mono>
            )}
            {contract.line != null ? (
              <span className="ml-2 text-xs tabular-nums text-[var(--color-text-tertiary)]">
                line {contract.line}
              </span>
            ) : null}
          </Fact>
          {contract.service ? (
            <Fact label="Service">
              <span className="text-xs">{contract.service}</span>
            </Fact>
          ) : null}
          {frameworkName ? (
            <Fact label="Framework">
              <span className="text-xs">{frameworkName}</span>
            </Fact>
          ) : client ? (
            <Fact label="Client">
              <span className="text-xs">{client}</span>
            </Fact>
          ) : null}
          <Fact label="Confidence">
            <span className="text-xs tabular-nums">{Math.round(contract.confidence * 100)}%</span>
          </Fact>
        </Facts>

        <Section title={isProvider ? "Callers" : "Served by"}>
          {links.length === 0 ? (
            <p className="text-[12.5px] leading-relaxed text-[var(--color-text-tertiary)]">
              {isProvider
                ? "Nothing in this workspace resolves to this contract. A call from inside the same repository and service is excluded by construction, and a call written in a form extraction could not follow looks the same from here."
                : "This call matched no declaration in the workspace. The full page names the reason recorded for it."}
            </p>
          ) : (
            <ul className="flex flex-col gap-2.5">
              {links.map((link) => (
                <CounterpartRow
                  key={`${link.provider_repo}|${link.provider_file}|${link.consumer_repo}|${link.consumer_file}`}
                  link={link}
                  side={isProvider ? "consumer" : "provider"}
                  codeLinks={codeLinks}
                />
              ))}
            </ul>
          )}
        </Section>

        {schema && (schema.request_fields.length > 0 || schema.response_fields.length > 0) ? (
          <Section title="Shape">
            <p className="text-[12.5px] leading-relaxed text-[var(--color-text-tertiary)]">
              Recovered by the {schema.source} reader. Fields are what the declaration names, not
              what any one caller passes.
            </p>
            {schema.request_fields.length > 0 ? (
              <FieldList caption="Request" fields={schema.request_fields} />
            ) : null}
            {schema.response_fields.length > 0 ? (
              <FieldList caption="Response" fields={schema.response_fields} />
            ) : null}
          </Section>
        ) : null}

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

        {metaEntries.length > 0 ? (
          <Section title="How it was found">
            <Facts>
              {metaEntries.map(([key, value]) => (
                <Fact key={key} label={contractMetaLabel(key)}>
                  <Mono>{value}</Mono>
                </Fact>
              ))}
            </Facts>
          </Section>
        ) : null}
      </div>

      {fullPageHref ? (
        <div className="border-t border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-5 py-3">
          <a
            href={fullPageHref}
            className="text-xs font-medium text-[var(--color-accent-primary)] hover:underline"
          >
            Open full page
          </a>
        </div>
      ) : null}
    </>
  );
}

/** The other side of one matched link: where it lives and what code it is. */
function CounterpartRow({
  link,
  side,
  codeLinks,
}: {
  link: WorkspaceContractLinkEntry;
  side: "provider" | "consumer";
  codeLinks?: BreakingChangeLinks | undefined;
}) {
  const repo = side === "provider" ? link.provider_repo : link.consumer_repo;
  const file = side === "provider" ? link.provider_file : link.consumer_file;
  const symbol = side === "provider" ? link.provider_symbol : link.consumer_symbol;
  const symbolId = side === "provider" ? link.provider_symbol_id : link.consumer_symbol_id;
  // The symbol page is the better destination when the side bound to one; the
  // file page is the fallback, and plain text when the repo has no index.
  const href =
    (symbolId ? codeLinks?.symbolHref?.(repo, symbolId) : null) ??
    codeLinks?.fileHref?.(repo, file) ??
    null;

  return (
    <li className="min-w-0">
      <div className="text-xs font-medium text-[var(--color-text-primary)]">{repo}</div>
      {href ? (
        <a href={href} className="text-[var(--color-accent-primary)] hover:underline">
          <Mono>{file}</Mono>
        </a>
      ) : (
        <Mono>{file}</Mono>
      )}
      {symbol ? (
        <div className="mt-0.5 text-[var(--color-text-tertiary)]">
          <Mono>{symbol}</Mono>
        </div>
      ) : null}
      <div className="mt-0.5 text-[11px] text-[var(--color-text-tertiary)]">
        {link.match_type} match ·{" "}
        <span className="tabular-nums">{Math.round(link.confidence * 100)}%</span>
      </div>
    </li>
  );
}

function FieldList({ caption, fields }: { caption: string; fields: SchemaField[] }) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium text-[var(--color-text-secondary)]">{caption}</div>
      <ul className="flex flex-col">
        {fields.map((f, i) => (
          <li
            key={`${f.name}|${f.number ?? i}`}
            className="flex flex-wrap items-baseline gap-x-2 border-t border-[var(--color-border-default)] py-1"
          >
            <span className="font-mono text-xs text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
              {f.name}
              {f.repeated ? (
                <span className="text-[var(--color-text-tertiary)]"> (repeated)</span>
              ) : null}
            </span>
            <span className="font-mono text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
              {f.type}
            </span>
            <span className="text-[11px] text-[var(--color-text-tertiary)]">
              {f.required ? "Required" : "Optional"}
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
      <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)] sm:pt-1">
        {label}
      </dt>
      {/* min-w-0 or a long path refuses to shrink and pushes the panel wide. */}
      <dd className="m-0 min-w-0 text-[var(--color-text-secondary)]">{children}</dd>
    </div>
  );
}

function Mono({ children }: { children: ReactNode }) {
  return <span className="font-mono text-xs [overflow-wrap:anywhere]">{children}</span>;
}
