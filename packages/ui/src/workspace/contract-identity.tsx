/**
 * How a contract and a file read in a row. Paths get a break opportunity after
 * each `/`, so they wrap between segments and a segment is split only when it
 * alone is wider than its column. No `"use client"`: server and client rows
 * both render them.
 */

import { Fragment } from "react";
import { parseContractId } from "./contract-facts";

/** A slash-separated string that wraps only after its slashes. */
export function SlashBreaks({ text }: { text: string }) {
  const segments = text.split("/");
  return (
    <>
      {segments.map((segment, i) => (
        <Fragment key={i}>
          {segment}
          {i < segments.length - 1 ? (
            <>
              /<wbr />
            </>
          ) : null}
        </Fragment>
      ))}
    </>
  );
}

const KIND_TAG: Record<string, string> = {
  data: "TABLE",
  grpc: "RPC",
  topic: "TOPIC",
  socket: "SOCKET",
};

/**
 * The contract as a reader names it: the verb as a small mono label, then the
 * path, table or symbol. `code` contracts carry their package underneath.
 */
export function ContractIdentity({
  contractId,
  size = "row",
  className,
}: {
  contractId: string;
  /** `title` sets the label at the drawer heading's 15px. */
  size?: "row" | "title";
  className?: string;
}) {
  const { kind, method, label, qualifier } = parseContractId(contractId);
  // HTTP rows carry their verb; other non-code kinds carry the kind, so a
  // table name never reads as a path with its verb missing.
  const tag = method ? (method === "*" ? "ANY" : method) : KIND_TAG[kind] ?? null;
  return (
    <span
      className={`inline-flex min-w-0 items-baseline gap-2 ${className ?? ""}`}
      title={contractId}
    >
      {tag ? (
        <span className="shrink-0 font-mono text-[10px] font-medium uppercase tracking-[0.06em] text-[var(--color-text-tertiary)]">
          {tag}
        </span>
      ) : null}
      <span className="min-w-0 [word-break:normal] [overflow-wrap:break-word]">
        <span
          className={`font-mono text-[var(--color-text-primary)] ${
            size === "title" ? "text-[15px] font-semibold" : "text-xs"
          }`}
        >
          <SlashBreaks text={label} />
        </span>
        {qualifier ? (
          <span className="block font-mono text-[10px] text-[var(--color-text-tertiary)]">
            {qualifier}
          </span>
        ) : null}
      </span>
    </span>
  );
}

/**
 * A repo-relative file: the directory dim, the file name at reading weight,
 * the line when known. The full path rides on `title`.
 */
export function FilePathText({
  path,
  line,
  accent = false,
}: {
  path: string;
  line?: number | null | undefined;
  /** The file name in the link colour, for a path that is a link. */
  accent?: boolean;
}) {
  const cut = path.lastIndexOf("/");
  const dir = cut >= 0 ? path.slice(0, cut + 1) : "";
  const name = cut >= 0 ? path.slice(cut + 1) : path;
  return (
    <span
      className="font-mono text-xs [word-break:normal] [overflow-wrap:break-word]"
      title={line != null ? `${path}:${line}` : path}
    >
      {dir ? (
        <span className="text-[var(--color-text-tertiary)]">
          <SlashBreaks text={dir} />
        </span>
      ) : null}
      <span
        className={
          accent ? "text-[var(--color-accent-primary)]" : "text-[var(--color-text-secondary)]"
        }
      >
        {name}
      </span>
      {line != null ? (
        <span className="tabular-nums text-[var(--color-text-tertiary)]">:{line}</span>
      ) : null}
    </span>
  );
}

/**
 * The service, unless the file path already says it. Services are usually a
 * directory (`packages/api-client`), and printing one above a path that starts
 * with it says the same thing twice.
 */
export function distinctService(service: string | null | undefined, file: string): string | null {
  if (!service) return null;
  return file === service || file.startsWith(`${service}/`) ? null : service;
}
