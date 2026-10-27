/**
 * How a contract reads in a row. Its label gets a break opportunity after
 * each `/`, so it wraps between segments and a segment is split only when it
 * alone is wider than its column.
 */

import { FilePath, SlashBreaks } from "../shared/file-path";
import { parseContractId } from "./contract-facts";

/** @deprecated Import `SlashBreaks` from `shared/file-path`; kept so existing imports compile. */
export { SlashBreaks };

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
 * @deprecated Use `<FilePath>` from `shared/file-path`, picking the mode for
 * the surface. Kept with its props so existing callers compile.
 */
export function FilePathText({
  path,
  line,
  accent = false,
}: {
  path: string;
  line?: number | null | undefined;
  accent?: boolean;
}) {
  return <FilePath path={path} line={line} mode="full" accent={accent} />;
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
