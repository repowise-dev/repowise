"use client";

import type { ReactNode } from "react";

export function Section({ title, children }: { title?: string; children: ReactNode }) {
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

export function Title({ children, style }: { children: ReactNode; style?: React.CSSProperties }) {
  return <div style={{ fontWeight: 600, fontSize: 13, ...style }}>{children}</div>;
}

export function Sub({ children, style }: { children: ReactNode; style?: React.CSSProperties }) {
  return <div style={{ opacity: 0.75, fontSize: 11, lineHeight: 1.4, ...style }}>{children}</div>;
}

export function KVList({ rows }: { rows: [string, string][] }) {
  return (
    <div style={{ marginTop: 6, flex: 1, minWidth: 0 }}>
      {rows.map(([k, v]) => (
        <div
          key={k}
          style={{
            display: "flex",
            justifyContent: "space-between",
            gap: 8,
            padding: "3px 0",
            borderTop: "1px solid var(--color-border-subtle)",
            fontSize: 11,
          }}
        >
          <span style={{ opacity: 0.6, whiteSpace: "nowrap" }}>{k}</span>
          <span style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", textAlign: "right", whiteSpace: "nowrap" }}>{v}</span>
        </div>
      ))}
    </div>
  );
}

export function ActionRow({ children }: { children: ReactNode }) {
  return <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>{children}</div>;
}

export function ActionButton({
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

export function Badge({
  label,
  color,
  bg,
}: {
  label: string;
  color?: string;
  bg?: string;
}) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 6px",
        borderRadius: 4,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: 0.4,
        color: color ?? "var(--color-text-primary)",
        background: bg ?? "var(--color-border-default)",
        textTransform: "uppercase",
      }}
    >
      {label}
    </span>
  );
}

export function Pill({ label }: { label: string }) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 10,
        fontSize: 10,
        fontWeight: 500,
        background: "var(--color-bg-wash-hover)",
        color: "var(--color-text-secondary)",
        border: "1px solid var(--color-border-default)",
      }}
    >
      {label}
    </span>
  );
}
