"use client";

import { useEffect, useRef, useState } from "react";

interface MermaidDiagramProps {
  chart: string;
}

export function MermaidDiagram({ chart }: MermaidDiagramProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const el = ref.current;

    import("mermaid").then(({ default: mermaid }) => {
      mermaid.initialize({
        startOnLoad: false,
        theme: "dark",
        themeVariables: {
          background: "#1a1a2e",
          primaryColor: "#5B9CF6",
          primaryTextColor: "#e2e8f0",
          lineColor: "#334155",
        },
      });

      const id = `mermaid-${Math.random().toString(36).slice(2)}`;
      mermaid
        .render(id, chart)
        .then(({ svg }) => {
          el.innerHTML = svg;
          const svgEl = el.querySelector("svg");
          if (svgEl) {
            svgEl.removeAttribute("width");
            svgEl.removeAttribute("height");
            svgEl.style.maxWidth = "100%";
            svgEl.style.height = "auto";
          }
        })
        .catch((e: unknown) => {
          setError(e instanceof Error ? e.message : "Diagram render failed");
        });
    });
  }, [chart]);

  if (error) {
    return (
      <div className="rounded border border-[var(--color-border-default)] p-3 text-xs text-[var(--color-outdated)]">
        Mermaid error: {error}
      </div>
    );
  }

  return (
    <figure
      role="img"
      aria-label="Mermaid diagram"
      className="my-4 flex justify-center overflow-x-auto rounded border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4"
    >
      <div ref={ref} className="w-full" />
    </figure>
  );
}
