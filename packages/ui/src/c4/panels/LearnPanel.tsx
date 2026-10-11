"use client";

import { BookOpen, Compass, FileCode, Server } from "lucide-react";
import { useArchitectureStore } from "../store/use-architecture-store";
import type { ArchTourStepKind } from "../types";
import { Section, ActionButton, Pill } from "./panel-atoms";

/** Iconography per curated step kind (viewer plan C-2). */
function KindIcon({ kind }: { kind: ArchTourStepKind }) {
  const style = { flexShrink: 0, opacity: 0.8 } as const;
  if (kind === "overview") return <BookOpen size={13} style={style} aria-label="Overview step" />;
  if (kind === "infra") return <Server size={13} style={style} aria-label="Infrastructure step" />;
  if (kind === "code") return <FileCode size={13} style={style} aria-label="Code step" />;
  return null;
}

export function LearnPanel() {
  const view = useArchitectureStore((s) => s.view);
  const tourActive = useArchitectureStore((s) => s.tourActive);
  const currentTourStep = useArchitectureStore((s) => s.currentTourStep);
  const startTour = useArchitectureStore((s) => s.startTour);
  const endTour = useArchitectureStore((s) => s.endTour);
  const nextTourStep = useArchitectureStore((s) => s.nextTourStep);
  const prevTourStep = useArchitectureStore((s) => s.prevTourStep);
  const goToTourStep = useArchitectureStore((s) => s.goToTourStep);
  const selectNode = useArchitectureStore((s) => s.selectNode);

  const tour = view?.tour;

  if (!tour || tour.length === 0) {
    return (
      <Section>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 8,
            padding: "24px 12px",
            opacity: 0.6,
          }}
        >
          <Compass size={24} />
          <span style={{ fontSize: 12 }}>No guided tour available</span>
        </div>
      </Section>
    );
  }

  if (!tourActive) {
    return (
      <Section>
        <div style={{ padding: "8px 0" }}>
          <div style={{ fontSize: 12, marginBottom: 8, opacity: 0.7 }}>
            {tour.length} steps in guided tour
          </div>
          <ActionButton onClick={startTour} variant="primary">
            Start Tour
          </ActionButton>
          <div style={{ marginTop: 12 }}>
            {tour.map((step, i) => (
              <div
                key={step.order}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "4px 0",
                  fontSize: 11,
                  opacity: 0.7,
                }}
              >
                <span
                  style={{
                    width: 18,
                    height: 18,
                    borderRadius: "50%",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 9,
                    fontWeight: 600,
                    background: "var(--color-bg-wash-hover)",
                    flexShrink: 0,
                  }}
                >
                  {i + 1}
                </span>
                <KindIcon kind={step.kind} />
                <span>{step.title}</span>
              </div>
            ))}
          </div>
        </div>
      </Section>
    );
  }

  const step = tour[currentTourStep];
  const progress = ((currentTourStep + 1) / tour.length) * 100;
  // Curated steps carry the "why this stop" prose in `reason` (plan C-2);
  // legacy LLM tours keep their description.
  const stepBody = step?.reason || step?.description || "";
  const stepLayer = step?.layer_id
    ? view?.layers.find((l) => l.id === step.layer_id)
    : undefined;

  return (
    <Section>
      <div style={{ padding: "4px 0" }}>
        {/* Progress bar */}
        <div
          style={{
            height: 3,
            borderRadius: 2,
            background: "var(--color-bg-wash-hover)",
            marginBottom: 12,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              width: `${progress}%`,
              height: "100%",
              background: "var(--color-accent-primary)",
              borderRadius: 2,
              transition: "width 0.3s ease",
            }}
          />
        </div>

        {/* Step title + kind icon */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: 600, fontSize: 14, marginBottom: 4 }}>
          {step && <KindIcon kind={step.kind} />}
          <span>{step?.title}</span>
        </div>

        {/* Layer the step belongs to (the canvas follows it) */}
        {stepLayer && (
          <div style={{ marginBottom: 6 }}>
            <Pill label={stepLayer.name} />
          </div>
        )}

        {/* Step body: curated reason, or legacy description */}
        <div style={{ fontSize: 12, opacity: 0.8, lineHeight: 1.5, marginBottom: 10 }}>
          {stepBody}
        </div>

        {/* Referenced nodes */}
        {step?.node_ids && step.node_ids.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 12 }}>
            {step.node_ids.map((nodeId) => (
              <button
                key={nodeId}
                type="button"
                onClick={() => selectNode(nodeId)}
                style={{
                  background: "transparent",
                  border: "none",
                  padding: 0,
                  cursor: "pointer",
                }}
              >
                <Pill label={nodeId.split("/").pop() ?? nodeId} />
              </button>
            ))}
          </div>
        )}

        {/* Prev / Next buttons */}
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10 }}>
          <ActionButton
            onClick={prevTourStep}
            variant="ghost"
          >
            Prev
          </ActionButton>
          <ActionButton
            onClick={nextTourStep}
            variant="ghost"
          >
            Next
          </ActionButton>
        </div>

        {/* Dot navigation */}
        <div style={{ display: "flex", gap: 4, alignItems: "center", marginBottom: 10 }}>
          {tour.map((_, i) => (
            <button
              key={i}
              type="button"
              onClick={() => goToTourStep(i)}
              style={{
                width: i === currentTourStep ? 8 : 6,
                height: i === currentTourStep ? 8 : 6,
                borderRadius: "50%",
                background:
                  i === currentTourStep
                    ? "var(--color-accent-primary)"
                    : "var(--color-border-default)",
                border: "none",
                cursor: "pointer",
                padding: 0,
                transition: "all 0.2s",
              }}
              aria-label={`Go to step ${i + 1}`}
            />
          ))}
        </div>

        {/* End tour */}
        <ActionButton onClick={endTour} variant="ghost">
          End Tour
        </ActionButton>
      </div>
    </Section>
  );
}
