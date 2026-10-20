import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { OwnerListEntry, OwnerProfile } from "@repowise-dev/types/owners";

import { OwnerDirectory } from "../../src/owners/owner-directory";
import { OwnerProfileView } from "../../src/owners/owner-profile";
import { OwnerTable } from "../../src/owners/owner-table";

const profile: OwnerProfile = {
  key: "ana@x.dev",
  name: "Ana",
  email: "ana@x.dev",
  kind: "human",
  aliases: { names: ["Ana"], emails: ["ana@x.dev", "ana@old.dev"] },
  files_owned: 2,
  hotspots_owned: 0,
  silo_modules: 0,
  dead_code_files_owned: 0,
  dead_code_lines_owned: 0,
  commit_count_90d: 3,
  last_commit_at: null,
  first_commit_at: null,
  bus_factor_risk_files: 0,
  lines_added_90d_est: 0,
  lines_deleted_90d_est: 0,
  modules: [{ module_path: "src", file_count: 2, hotspot_count: 0, dominant_pct: 0.5 }],
  top_files: [],
  co_authors: [],
  commit_categories: {},
};

const owner: OwnerListEntry = {
  key: "bot",
  name: "renovate[bot]",
  email: null,
  kind: "bot",
  files_owned: 1,
  hotspots_owned: 0,
  silo_modules: 0,
  dead_code_files_owned: 0,
  dead_code_lines_owned: 0,
  commit_count_90d: 1,
  last_commit_at: null,
  bus_factor_risk_files: 0,
};

describe("ownership labels on the person page and the people table", () => {
  it("renders English defaults without labels", () => {
    render(<OwnerProfileView owner={profile} base="/repos/1" dependsOn={null} />);
    expect(screen.getByText("What depends on them")).toBeTruthy();
    expect(screen.getByText("Where they work")).toBeTruthy();
    expect(screen.getByText("Active: 3 commits in the last 90 days")).toBeTruthy();
    expect(screen.getByText("ana@old.dev")).toBeTruthy();
  });

  it("renders the host's labels", () => {
    render(
      <OwnerProfileView
        owner={profile}
        base="/repos/1"
        dependsOn={null}
        labels={{
          dependsTitle: "依赖于他们的代码",
          footprintTitle: "他们的工作范围",
          activeLine: (n) => `活跃：${n}`,
          aliasesLabel: "其他提交身份",
        }}
      />,
    );
    expect(screen.getByText("依赖于他们的代码")).toBeTruthy();
    expect(screen.getByText("他们的工作范围")).toBeTruthy();
    expect(screen.getByText("活跃：3")).toBeTruthy();
    expect(screen.getByText("其他提交身份")).toBeTruthy();
  });

  it("labels the table's columns and automation kinds", () => {
    render(
      <OwnerTable owners={[owner]} sort="files_owned" labels={{ colContributor: "贡献者", kindBot: "机器人" }} />,
    );
    expect(screen.getByText("贡献者")).toBeTruthy();
    expect(screen.getByText("机器人")).toBeTruthy();
    expect(screen.getByText("Files owned")).toBeTruthy();
  });
});

describe("payloads and callers from the previous release", () => {
  it("renders a profile with no kind, no aliases and no dependsOn", () => {
    const { kind: _k, aliases: _a, ...older } = profile;
    render(<OwnerProfileView owner={older} base="/repos/1" />);
    expect(screen.queryByText("What depends on them")).toBeNull();
    expect(screen.queryByText("Also commits as")).toBeNull();
    expect(screen.getByText("Where they work")).toBeTruthy();
  });

  it("shows no kind badge when the row carries no kind", () => {
    const { kind: _k, ...older } = owner;
    render(<OwnerTable owners={[older]} sort="files_owned" />);
    expect(screen.getByText("renovate[bot]")).toBeTruthy();
    expect(screen.queryByText("Bot")).toBeNull();
    expect(screen.queryByText("Person")).toBeNull();
  });

  it("accepts the older directory props and still loads more", () => {
    let loads = 0;
    render(
      <OwnerDirectory
        owners={[owner]}
        distributionOwners={[owner]}
        base="/repos/1"
        isLoading={false}
        isValidating={false}
        total={40}
        hasMore
        filters={{ q: "", sort: "files_owned" }}
        onFiltersChange={() => {}}
        onLoadMore={() => (loads += 1)}
        onSelect={() => {}}
      />,
    );
    fireEvent.click(screen.getByText("Show all 40 people"));
    expect(loads).toBe(1);
  });
});
