// SPDX-License-Identifier: Unlicense
import { describe, expect, it } from "vitest";
import { place } from "./tooltip";

const VIEW = { width: 1000, height: 720 };
const at = (left: number, top: number) => ({ left, top, width: 40, height: 24 });

describe("place", () => {
  it("opens above, centred on the element", () => {
    expect(place(at(480, 300), 120, 30, VIEW)).toEqual({ left: 440, top: 264, below: false });
  });

  it("flips below when there is no room above", () => {
    const box = place(at(480, 10), 120, 30, VIEW);
    expect(box.below).toBe(true);
    expect(box.top).toBe(40);
  });

  it("opens below when asked and there is room", () => {
    expect(place(at(480, 300), 120, 30, VIEW, true).below).toBe(true);
  });

  it("goes above when below there is no room, also when below was asked for", () => {
    expect(place(at(480, 690), 120, 30, VIEW, true).below).toBe(false);
  });

  it("stays inside the window's left and right edges", () => {
    expect(place(at(0, 300), 200, 30, VIEW).left).toBe(8);
    expect(place(at(990, 300), 200, 30, VIEW).left).toBe(1000 - 8 - 200);
  });

  it("stays inside the window when it is taller than either side", () => {
    const box = place(at(480, 340), 120, 600, VIEW);
    expect(box.top).toBeGreaterThanOrEqual(8);
    expect(box.top + 600).toBeLessThanOrEqual(720 - 8);
  });
});
