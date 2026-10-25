import { useId } from "react";
import type {
  CardBasis,
  CardBasisKind,
  CardRef,
  EvidenceCard,
  EvidenceCardKind,
} from "@repowise-dev/types/cards";
import { fileEntityPath, symbolEntityPath } from "../shared/entity/routes";

const KIND_LABEL: Record<EvidenceCardKind, string> = {
  answer: "Answer",
  change: "Change",
  component: "Component",
  scope: "Scope",
  suspects: "Suspects",
  event: "Event",
};

const BASIS_LABEL: Record<CardBasisKind, string> = {
  measured: "Measured",
  inferred: "Inferred",
  declared: "Declared",
  unresolved: "Unresolved",
};

/**
 * The dashboard route for a card ref, or undefined when the dashboard has no
 * page for that ref type (it is then shown as plain text).
 */
export function cardRefHref(ref: CardRef, prefix: string): string | undefined {
  const id = encodeURIComponent(ref.id);
  switch (ref.type) {
    case "file":
      return fileEntityPath(prefix, ref.id);
    case "symbol":
      return symbolEntityPath(prefix, ref.id);
    case "decision":
      return `${prefix}/decisions/${id}`;
    case "commit":
      return `${prefix}/commits?commit=${id}`;
    default:
      return undefined;
  }
}

export function refLabel(ref: CardRef): string {
  const name =
    ref.type === "file"
      ? (ref.id.split("/").pop() ?? ref.id)
      : ref.type === "commit"
        ? ref.id.slice(0, 7)
        : ref.id;
  if (!ref.start_line) return name;
  return ref.end_line && ref.end_line !== ref.start_line
    ? `${name}:${ref.start_line}-${ref.end_line}`
    : `${name}:${ref.start_line}`;
}

function subject(card: EvidenceCard): string | CardRef {
  switch (card.kind) {
    case "answer":
      return card.question;
    case "change":
      return card.change;
    case "component":
      return card.component;
    case "scope":
      return card.request;
    case "suspects":
      return card.incident;
    case "event":
      return card.event_type;
  }
}

export type RefHref = (ref: CardRef) => string | undefined;

export function RefView({ cardRef, buildRefHref }: { cardRef: CardRef; buildRefHref?: RefHref | undefined }) {
  const href = buildRefHref?.(cardRef);
  const label = refLabel(cardRef);
  const className = "font-mono text-xs [overflow-wrap:anywhere]";
  return href ? (
    <a
      href={href}
      title={cardRef.id}
      className={`${className} text-[var(--color-accent-primary)] rounded-sm underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]`}
    >
      {label}
    </a>
  ) : (
    <code title={cardRef.id} className={`${className} text-[var(--color-text-secondary)]`}>
      {label}
    </code>
  );
}

/** Basis is spelled out, never colour-coded, so it reads the same in every theme. */
export function BasisNote({ basis }: { basis: CardBasis }) {
  return (
    <span data-card-basis={basis.kind} className="text-xs text-[var(--color-text-tertiary)]">
      <span className="sr-only">Basis: </span>
      {BASIS_LABEL[basis.kind]} · {basis.source}
    </span>
  );
}

export interface EvidenceCardViewProps {
  card: EvidenceCard;
  /** Resolves a ref to a link; refs it returns undefined for render as text. */
  buildRefHref?: RefHref;
}

export function EvidenceCardView({ card, buildRefHref }: EvidenceCardViewProps) {
  const titleId = useId();
  const about = subject(card);
  return (
    <section
      aria-labelledby={titleId}
      data-card-kind={card.kind}
      className="min-w-0 space-y-2.5 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3 text-sm text-[var(--color-text-primary)]"
    >
      <header className="space-y-0.5">
        <p className="text-xs text-[var(--color-text-tertiary)]">
          {KIND_LABEL[card.kind]}
          {card.confidence && <> · {card.confidence} confidence</>}
        </p>
        <h3 id={titleId} className="font-medium [overflow-wrap:anywhere]">
          {card.title}
        </h3>
        <p className="text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
          {typeof about === "string" ? about : <RefView cardRef={about} buildRefHref={buildRefHref} />}
        </p>
      </header>

      {card.summary && <p className="text-[var(--color-text-secondary)]">{card.summary}</p>}

      {card.facts.length > 0 && (
        <dl className="grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-3 gap-y-1.5">
          {card.facts.map((fact, i) => (
            <div key={i} className="contents">
              <dt className="text-[var(--color-text-secondary)]">{fact.label}</dt>
              <dd className="min-w-0 space-x-2">
                <span className="tabular-nums">
                  {typeof fact.value === "number" ? fact.value.toLocaleString() : fact.value}
                </span>
                {fact.ref && <RefView cardRef={fact.ref} buildRefHref={buildRefHref} />}
                <BasisNote basis={fact.basis} />
              </dd>
            </div>
          ))}
        </dl>
      )}

      {card.evidence.length > 0 && (
        <ul role="list" aria-label="Evidence" className={card.kind === "suspects" ? "list-decimal space-y-1.5 pl-5" : "space-y-1.5"}>
          {card.evidence.map((row, i) => (
            <li key={i} className="min-w-0">
              <p className="[overflow-wrap:anywhere]">{row.statement}</p>
              <p className="flex flex-wrap items-baseline gap-x-2">
                <BasisNote basis={row.basis} />
                {row.refs?.map((ref, j) => (
                  <RefView key={j} cardRef={ref} buildRefHref={buildRefHref} />
                ))}
              </p>
            </li>
          ))}
        </ul>
      )}

      {card.links.length > 0 && (
        <ul role="list" aria-label="Links" className="flex flex-wrap gap-x-3 gap-y-1">
          {card.links.map((link, i) => {
            const href = buildRefHref?.(link.ref);
            return (
              <li key={i} className="text-xs">
                {href ? (
                  <a href={href} className="text-[var(--color-accent-primary)] rounded-sm underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]">
                    {link.label}
                  </a>
                ) : (
                  <span className="text-[var(--color-text-secondary)]">{link.label}</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export function EvidenceCardList({
  cards,
  buildRefHref,
}: {
  cards: EvidenceCard[];
  buildRefHref?: RefHref;
}) {
  if (cards.length === 0) return null;
  return (
    <div className="space-y-2">
      {cards.map((card, i) => (
        <EvidenceCardView key={i} card={card} {...(buildRefHref ? { buildRefHref } : {})} />
      ))}
    </div>
  );
}
