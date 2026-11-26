// SPDX-License-Identifier: Unlicense
import { describe, expect, it } from "vitest";
import { channelName, shortSoc, shortVersion, versionLine } from "./words";

describe("short words", () => {
  it("shortens the SoC", () => {
    expect(shortSoc("Qualcomm Snapdragon XR2 (SM8250)")).toBe("Snapdragon XR2");
    expect(shortSoc("")).toBe("—");
  });

  it("shortens what is downloaded", () => {
    expect(shortVersion("release 2026.11.0b1")).toBe("2026.11.0b1");
    expect(shortVersion("preview 2026.12.0 preview from 3 December 2026 (4d8f977)")).toBe("Preview");
    expect(shortVersion("development checkout")).toBe("Checkout");
    expect(shortVersion("")).toBe("—");
  });

  it("makes a tile's version line", () => {
    expect(channelName("beta")).toBe("Beta");
    expect(versionLine("release 2026.11.0b1", "beta")).toBe("2026.11.0b1 · Beta");
    expect(versionLine("development checkout", "")).toBe("Checkout");
  });
});
