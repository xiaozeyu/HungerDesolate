import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { createSerialQueue } from "./serialQueue.ts";

describe("serialized remote query queue", () => {
  it("runs remote operations in order and continues after a failed operation", async () => {
    const run = createSerialQueue();
    const order: string[] = [];
    let releaseFirst!: () => void;
    const first = run(() => new Promise<void>((resolve) => {
      order.push("first-start");
      releaseFirst = () => {
        order.push("first-end");
        resolve();
      };
    }));
    const second = run(async () => {
      order.push("second");
      throw new Error("remote failure");
    });
    const third = run(async () => {
      order.push("third");
      return "done";
    });

    await Promise.resolve();
    assert.deepEqual(order, ["first-start"]);
    releaseFirst();
    await first;
    await assert.rejects(second, /remote failure/);
    assert.equal(await third, "done");
    assert.deepEqual(order, ["first-start", "first-end", "second", "third"]);
  });
});
