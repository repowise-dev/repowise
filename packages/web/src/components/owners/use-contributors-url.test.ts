import { describe, expect, it } from "vitest";

import { directoryFiltersKey, externalDirectoryFilters } from "./use-contributors-url";

const shown = { q: "ada", sort: "files_owned" as const, includeAutomation: false };

describe("externalDirectoryFilters", () => {
  it("ignores the table's own write and a URL that already says what it shows", () => {
    const own = directoryFiltersKey(shown);
    expect(externalDirectoryFilters(new URLSearchParams(`tab=overview&${own}`), [own], shown)).toBeNull();
    expect(externalDirectoryFilters(new URLSearchParams("q=ada"), [], shown)).toBeNull();
    // A late echo of an earlier write, while a newer one is pending.
    expect(externalDirectoryFilters(new URLSearchParams("q=a"), ["q=a", own], shown)).toBeNull();
  });

  it("adopts a URL the table did not write", () => {
    const own = directoryFiltersKey(shown);
    expect(externalDirectoryFilters(new URLSearchParams(""), [own], shown)).toEqual({
      q: "",
      sort: "files_owned",
      includeAutomation: false,
    });
    expect(externalDirectoryFilters(new URLSearchParams("automation=1"), [own], shown)).toMatchObject({
      includeAutomation: true,
    });
  });
});
