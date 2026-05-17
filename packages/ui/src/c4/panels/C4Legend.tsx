"use client";

const ITEMS: { label: string; color: string }[] = [
  { label: "System",    color: "#1f3a8a" },
  { label: "Person",    color: "#374151" },
  { label: "Container", color: "#0f3a5f" },
  { label: "Component", color: "#0d2a47" },
  { label: "External",  color: "#1f2937" },
];

export function C4Legend() {
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 10,
        padding: "6px 10px",
        fontSize: 11,
        color: "var(--color-text-secondary, #94a3b8)",
        background: "var(--color-bg-surface, rgba(15,23,42,0.7))",
        border: "1px solid var(--color-border-default, #334155)",
        borderRadius: 6,
      }}
    >
      {ITEMS.map((it) => (
        <span key={it.label} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
          <span
            aria-hidden
            style={{
              width: 10,
              height: 10,
              borderRadius: 2,
              background: it.color,
              border: "1px solid rgba(148,163,184,0.4)",
            }}
          />
          {it.label}
        </span>
      ))}
    </div>
  );
}
