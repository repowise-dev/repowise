"use client";

import { useEffect, useState } from "react";
import { cn } from "../lib/cn";

interface Heading {
  id: string;
  text: string;
  level: number;
}

function extractHeadings(content: string): Heading[] {
  const lines = content.split("\n");
  const headings: Heading[] = [];
  for (const line of lines) {
    const match = line.match(/^(#{1,3})\s+(.+)$/);
    if (match && match[1] && match[2]) {
      const text = match[2].trim();
      const id = text
        .toLowerCase()
        .replace(/[^\w\s-]/g, "")
        .replace(/\s+/g, "-");
      headings.push({ id, text, level: match[1].length });
    }
  }
  return headings;
}

export function TableOfContents({ content }: { content: string }) {
  const [activeId, setActiveId] = useState<string>("");
  const headings = extractHeadings(content);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveId(entry.target.id);
          }
        }
      },
      { rootMargin: "0px 0px -70% 0px" },
    );

    headings.forEach(({ id }) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });

    return () => observer.disconnect();
  }, [headings]);

  if (headings.length < 2) return null;

  return (
    <nav aria-label="Table of contents">
      <p className="text-xs font-semibold text-[var(--color-text-primary)] mb-2">
        On this page
      </p>
      {/* Left rail: a hairline the active item's accent bar sits on. */}
      <ul className="border-l border-[var(--color-border-default)]">
        {headings.map(({ id, text, level }) => (
          <li key={id}>
            <a
              href={`#${id}`}
              className={cn(
                "block -ml-px border-l-2 py-1 text-[13px] leading-snug transition-colors hover:text-[var(--color-text-primary)]",
                level === 1 ? "pl-3" : level === 2 ? "pl-3" : "pl-6",
                activeId === id
                  ? "border-[var(--color-accent-primary)] font-medium text-[var(--color-accent-primary)]"
                  : "border-transparent text-[var(--color-text-tertiary)]",
              )}
            >
              {text}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
