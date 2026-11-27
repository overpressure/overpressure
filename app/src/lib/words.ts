// SPDX-License-Identifier: Unlicense
// Short words for tiles and facts: the long form goes into the tooltip.

/** "Qualcomm Snapdragon XR2 (SM8250)" -> "Snapdragon XR2". */
export function shortSoc(soc: string): string {
  return soc.replace(/^Qualcomm\s+/i, "").replace(/\s*\(.*\)\s*$/, "") || "—";
}

/** What is downloaded, short: "release 2026.11.0b1" -> "2026.11.0b1"; a preview, a branch, a checkout; "" -> "—". */
export function shortVersion(downloaded: string): string {
  if (!downloaded) return "—";
  if (downloaded === "development checkout") return "Checkout";
  if (downloaded.startsWith("preview")) return "Preview";
  if (downloaded.startsWith("branch ")) return downloaded.slice("branch ".length).split(":")[0] ?? "—";
  return downloaded.replace(/^release\s+/, "");
}

const CHANNELS: Record<string, string> = { stable: "Stable", beta: "Beta", preview: "Preview" };

/** "beta" -> "Beta"; "" -> "". */
export function channelName(channel: string): string {
  return CHANNELS[channel] ?? channel;
}

/** A tile's second line: "2026.11.0b1 · Beta". */
export function versionLine(downloaded: string, channel: string): string {
  return [shortVersion(downloaded), channelName(channel)].filter((w) => w && w !== "—").join(" · ");
}

const RUN_TITLES: Record<string, string> = {
  install: "Installing overpressure",
  upgrade: "Upgrading overpressure",
  restore: "Restoring the stock system",
  check: "Checking the headset",
  rescue: "Bringing the headset back",
};

/** A run's title by its workflow: "install" -> "Installing overpressure". */
export function runTitle(workflow: string): string {
  return RUN_TITLES[workflow] ?? workflow;
}
