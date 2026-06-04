"use client";

/**
 * Rich right-rail for the C4 view.
 *
 * Beyond the basic facts shown by C4NodeInspector, this panel optionally
 * surfaces "context-relevant" data the host has fetched:
 *
 *   - a documentation excerpt (wiki page) for the selected container/component
 *   - top contributors + recent activity (git ownership)
 *   - module health signals (hotspots, dead, doc coverage)
 *
 * The panel is intentionally lazy: every section is shown only if its data
 * is provided. The host (web page) wires the fetches; the UI package stays
 * dumb so the same panel works in the hosted frontend.
 */

import { ExternalLink, X } from "lucide-react";
import type { ReactNode } from "react";
import type { C4NodeData } from "../types";

export interface C4Health {
  health_score: number;
  hotspot_count: number;
  dead_code_count: number;
  doc_coverage_pct: number;
  primary_owner: string | null;
  primary_owner_pct: number;
  contributor_count?: number;
  is_silo?: boolean;
}

export interface C4DocSummary {
  title: string;
  excerpt: string;
  /** Optional href the host knows how to open (e.g. /repos/{id}/docs/[page]) */
  href?: string;
}

export interface C4DetailPanelProps {
  data: C4NodeData | null;
  loading?: boolean;

  doc?: C4DocSummary | null;
  health?: C4Health | null;
  contributors?: { name: string; files: number; pct?: number }[];

  /** Render rich docs (markdown) inline instead of the excerpt. Optional. */
  renderDoc?: (content: string) => ReactNode;
  docContent?: string | null;

  onClose: () => void;
  onDrillIn?: ((containerId: string) => void) | undefined;
  onOpenInGraph?: ((path: string) => void) | undefined;
  onOpenDoc?: ((href: string) => void) | undefined;
}

export function C4DetailPanel(props: C4DetailPanelProps) {
  const { data, onClose } = props;
  if (!data) return null;
  return (
    <aside
      aria-label="Selected node details"
      style={{
        position: "absolute",
        top: 12,
        right: 12,
        width: 360,
        maxHeight: "calc(100% - 24px)",
        overflow: "auto",
        padding: 0,
        background: "var(--color-bg-elevated, rgba(17,24,39,0.96))",
        border: "1px solid var(--color-border-default)",
        borderRadius: 8,
        color: "var(--color-text-primary)",
        fontSize: 12,
        zIndex: 5,
        display: "flex",
        flexDirection: "column",
        boxShadow: "0 10px 30px rgba(0,0,0,0.35)",
      }}
    >
      <Header data={data} onClose={onClose} />
      <Body {...props} />
    </aside>
  );
}

function Header({ data, onClose }: { data: C4NodeData; onClose: () => void }) {
  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "10px 12px",
        borderBottom: "1px solid var(--color-border-default)",
      }}
    >
      <span style={{ textTransform: "uppercase", fontSize: 10, letterSpacing: 0.6, opacity: 0.65, fontWeight: 600 }}>
        {data.kind}
      </span>
      <button
        type="button"
        aria-label="Close panel"
        onClick={onClose}
        style={{ background: "none", border: "none", cursor: "pointer", color: "inherit", padding: 2 }}
      >
        <X size={14} />
      </button>
    </header>
  );
}

function Body(props: C4DetailPanelProps) {
  const { data } = props;
  switch (data!.kind) {
    case "system":
      return (
        <Section>
          <Title>{data!.system.name}</Title>
          <Sub>{data!.system.description || "System under analysis"}</Sub>
        </Section>
      );
    case "person":
      return (
        <Section>
          <Title>{data!.person.name}</Title>
          {data!.person.description && <Sub>{data!.person.description}</Sub>}
        </Section>
      );
    case "external": {
      const e = data!.external;
      return (
        <Section>
          <Title>{e.display_name || e.name}</Title>
          <Sub>{e.ecosystem} · {e.category}</Sub>
          <KVList rows={[
            ["package", e.name],
            ...(e.version ? [["version", e.version] as [string, string]] : []),
          ]} />
        </Section>
      );
    }
    case "container":
    case "component":
      return <ContainerOrComponentBody {...props} />;
  }
}

function ContainerOrComponentBody(props: C4DetailPanelProps) {
  const { data, loading, doc, health, contributors, docContent, renderDoc, onDrillIn, onOpenDoc, onOpenInGraph } = props;
  if (data!.kind !== "container" && data!.kind !== "component") return null;
  const isContainer = data!.kind === "container";
  const node = isContainer ? data!.container : data!.component;
  const path = node.path;
  return (
    <>
      <Section>
        <Title>{node.name === "_root" ? "(root)" : node.name}</Title>
        <Sub style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)" }}>{path}</Sub>
        <ActionRow>
          {isContainer && onDrillIn && data!.kind === "container" && (
            <ActionButton onClick={() => onDrillIn(data!.container.id)}>
              Drill into components →
            </ActionButton>
          )}
          {onOpenInGraph && (
            <ActionButton onClick={() => onOpenInGraph(path)} variant="ghost">
              Open in dependency graph
            </ActionButton>
          )}
        </ActionRow>
      </Section>

      {loading && (
        <Section>
          <Sub>Loading details…</Sub>
        </Section>
      )}

      {health && (
        <Section title="Health">
          <KVList rows={[
            ["files", String(node.file_count)],
            ["symbols", String(node.symbol_count)],
            ["doc coverage", `${Math.round(health.doc_coverage_pct)}%`],
            ...(health.hotspot_count > 0 ? [["hotspots", String(health.hotspot_count)] as [string, string]] : []),
            ...(health.dead_code_count > 0 ? [["dead code", String(health.dead_code_count)] as [string, string]] : []),
          ]} />
          {health.primary_owner && (
            <Sub style={{ marginTop: 6 }}>
              Primary owner: <strong>{health.primary_owner}</strong>{" "}
              ({Math.round(health.primary_owner_pct * 100)}%)
              {health.is_silo && (
                <span style={{ marginLeft: 6, color: "var(--color-warning)" }}>· silo</span>
              )}
            </Sub>
          )}
        </Section>
      )}

      {contributors && contributors.length > 0 && (
        <Section title="Top contributors">
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {contributors.slice(0, 5).map((c) => (
              <li
                key={c.name}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  padding: "3px 0",
                  borderTop: "1px solid var(--color-border-subtle)",
                  fontSize: 11,
                }}
              >
                <span style={{ opacity: 0.9 }}>{c.name}</span>
                <span style={{ opacity: 0.6 }}>
                  {c.files} files
                  {c.pct != null && ` · ${Math.round(c.pct * 100)}%`}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {doc ? (
        <Section title="Documentation">
          <Title style={{ fontSize: 12 }}>{doc.title}</Title>
          {docContent && renderDoc ? (
            <div style={{ marginTop: 6, fontSize: 12, lineHeight: 1.45, color: "var(--color-text-secondary)" }}>
              {renderDoc(docContent)}
            </div>
          ) : (
            <Sub style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>{doc.excerpt}</Sub>
          )}
          {doc.href && onOpenDoc && (
            <ActionButton onClick={() => onOpenDoc(doc.href!)} variant="ghost" icon={ExternalLink}>
              Open full page
            </ActionButton>
          )}
        </Section>
      ) : (
        !loading && (
          <Section title="Documentation">
            <Sub style={{ opacity: 0.6 }}>No wiki page generated for this path yet.</Sub>
          </Section>
        )
      )}
    </>
  );
}

// --- tiny presentational atoms (keep panel body declarative) ---

function Section({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--color-border-subtle)" }}>
      {title && (
        <div
          style={{
            textTransform: "uppercase",
            fontSize: 10,
            letterSpacing: 0.6,
            opacity: 0.55,
            fontWeight: 600,
            marginBottom: 6,
          }}
        >
          {title}
        </div>
      )}
      {children}
    </div>
  );
}

function Title({ children, style }: { children: ReactNode; style?: React.CSSProperties }) {
  return <div style={{ fontWeight: 600, fontSize: 13, ...style }}>{children}</div>;
}

function Sub({ children, style }: { children: ReactNode; style?: React.CSSProperties }) {
  return <div style={{ opacity: 0.75, fontSize: 11, lineHeight: 1.4, ...style }}>{children}</div>;
}

function KVList({ rows }: { rows: [string, string][] }) {
  return (
    <div style={{ marginTop: 6 }}>
      {rows.map(([k, v]) => (
        <div
          key={k}
          style={{
            display: "flex",
            justifyContent: "space-between",
            padding: "3px 0",
            borderTop: "1px solid var(--color-border-subtle)",
            fontSize: 11,
          }}
        >
          <span style={{ opacity: 0.6 }}>{k}</span>
          <span style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)" }}>{v}</span>
        </div>
      ))}
    </div>
  );
}

function ActionRow({ children }: { children: ReactNode }) {
  return <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>{children}</div>;
}

function ActionButton({
  children,
  onClick,
  variant = "primary",
  icon: Icon,
}: {
  children: ReactNode;
  onClick: () => void;
  variant?: "primary" | "ghost";
  icon?: React.ComponentType<{ size?: number }>;
}) {
  const primary = variant === "primary";
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        padding: "5px 10px",
        background: primary ? "var(--color-accent-muted, rgba(245,149,32,0.2))" : "transparent",
        color: primary
          ? "var(--color-accent-primary)"
          : "var(--color-text-secondary)",
        border: `1px solid ${primary ? "var(--color-accent-primary)" : "var(--color-border-default)"}`,
        borderRadius: 4,
        cursor: "pointer",
        fontSize: 11,
        fontWeight: 500,
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
      }}
    >
      {Icon && <Icon size={11} />}
      {children}
    </button>
  );
}
