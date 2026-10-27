import { describe, expect, it } from "vitest";
import { isFileNodeId } from "../../src/graph/node-id";

describe("isFileNodeId", () => {
  it("accepts a bare file path only", () => {
    expect(isFileNodeId("src/a.ts")).toBe(true);
    expect(isFileNodeId("src/a.ts::load")).toBe(false);
    expect(isFileNodeId("external:react")).toBe(false);
  });
});
