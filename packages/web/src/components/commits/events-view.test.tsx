// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { createTranslator } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";
import messages from "../../../messages/en.json";
import type { CloudEvent, EventListResponse } from "@/lib/api/events";
import { repoNavItems } from "@/components/layout/nav-items";
import { parseCommitsView } from "@/lib/changes";

const listEvents = vi.fn();
vi.mock("@/lib/api/events", () => ({ listEvents: (...a: unknown[]) => listEvents(...a) }));
vi.mock("next-intl/server", () => ({
  getTranslations: async (namespace: string) =>
    createTranslator({ locale: "en", messages, namespace: namespace as never }),
}));

import { EventsView } from "./events-view";

function event(id: string, kind: string, time: string, component?: string): CloudEvent {
  return {
    specversion: "1.0",
    id,
    source: "/repos/r1",
    type: `dev.repowise.${kind}.0.1.0`,
    time,
    datacontenttype: "application/json",
    subject: component ? `component:${component}` : null,
    data: {
      "repowise.kind": kind,
      "repowise.severity": "high",
      ...(component ? { "repowise.component": component } : {}),
    },
  };
}

async function show(reply: EventListResponse) {
  listEvents.mockResolvedValue(reply);
  render(await EventsView({ repoId: "r1" }));
}

afterEach(() => {
  cleanup();
  listEvents.mockReset();
});

describe("EventsView", () => {
  it("lists events newest first with kind, component and severity in words", async () => {
    await show({
      events: [
        event("1", "new_hotspot", "2026-10-01T00:00:00Z", "core"),
        event("2", "bus_factor_drop", "2026-10-02T00:00:00Z"),
      ],
      snapshot_runs: 3,
    });
    const rows = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(rows[0]).toContain("Bus factor fell");
    expect(rows[0]).toContain("Whole repository");
    expect(rows[1]).toContain("Files became hotspots");
    expect(rows[1]).toContain("core");
    expect(rows[1]).toContain("high");
    expect(rows.join(" ")).not.toMatch(/new_hotspot|repowise\./);
  });

  it("says why an empty list is empty", async () => {
    await show({ events: [], snapshot_runs: 1, empty_reason: "baseline_only" });
    expect(screen.getByText(/One component snapshot is stored, the baseline/)).toBeTruthy();
    expect(screen.getByText(/start after the next `repowise update` on a new commit/)).toBeTruthy();
  });

  it("is a view of the Commits page, not a navigation entry", () => {
    expect(parseCommitsView("events")).toBe("events");
    expect(repoNavItems("r1").map((i) => i.href)).not.toContain("/repos/r1/events");
  });
});
