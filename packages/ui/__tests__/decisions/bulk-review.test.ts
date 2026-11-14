import { describe, expect, it, vi } from "vitest";
import type { DecisionRecord } from "@repowise-dev/types/decisions";
import { reviewEach } from "../../src/decisions/bulk-review";

const rec = (id: string) => ({ id, title: id }) as DecisionRecord;

describe("reviewEach", () => {
  it("writes every record, keeps going past a refusal and reports each", async () => {
    const write = vi.fn(async (d: DecisionRecord) => {
      if (d.id === "b") throw new Error("refused");
    });
    const progress: number[] = [];

    const result = await reviewEach([rec("a"), rec("b"), rec("c")], write, (n) => progress.push(n), 2);

    expect(write).toHaveBeenCalledTimes(3);
    expect(result.done.map((d) => d.id).sort()).toEqual(["a", "c"]);
    expect(result.failed).toEqual([{ decision: rec("b"), message: "refused" }]);
    expect(progress).toEqual([1, 2, 3]);
  });

  it("does nothing for an empty selection", async () => {
    const write = vi.fn();
    expect(await reviewEach([], write)).toEqual({ done: [], failed: [] });
    expect(write).not.toHaveBeenCalled();
  });
});
