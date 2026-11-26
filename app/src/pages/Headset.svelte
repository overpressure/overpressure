<!-- SPDX-License-Identifier: Unlicense -->
<!-- A headset: its picture and short facts, its workflows as plain tiles (an icon and a name; what each does in
     its tooltip, badges for erasing the headset and for why it cannot run now), then a row of tools: the backup's
     export and import, the logs, Advanced mode. The long words live in tooltips. -->
<script lang="ts">
  import { app } from "../lib/app.svelte";
  import { pickPath } from "../lib/bridge";
  import type { JobState } from "../lib/job";
  import type { Backup, HeadsetEntry, Workflow } from "../lib/protocol";
  import { channelName, shortSoc, shortVersion } from "../lib/words";
  import AddDialog from "../components/AddDialog.svelte";
  import Badge from "../components/Badge.svelte";
  import HeadsetArt from "../components/HeadsetArt.svelte";
  import Icon, { type IconName } from "../components/Icon.svelte";
  import JobStatus from "../components/JobStatus.svelte";

  let { headset }: { headset: HeadsetEntry } = $props();

  const ICONS: Record<string, IconName> = { install: "download", upgrade: "up", restore: "restore", check: "shield" };
  const BACKUP_SUFFIX = ".overpressure-backup.tar";

  let changing = $state(false);
  let backup = $state<Backup | null>(null);
  let job = $state<JobState | null>(null);

  const busy = $derived(app.running || app.jobRunning);
  const update = $derived(app.updates[headset.codename]);

  async function readBackup(): Promise<void> {
    backup = await app.engine.request<Backup>("backup", { codename: headset.codename });
  }

  $effect(() => {
    void headset.codename;
    void readBackup();
  });

  $effect(() => {
    if (job?.code === 0 && (job.kind === "export" || job.kind === "import")) void readBackup();
  });

  function blocked(w: Workflow): string {
    if (w.problem) return w.problem;
    if (app.running) return "A run is under way.";
    if (app.jobRunning) return "Something else is running. Let it finish first.";
    return "";
  }

  function press(w: Workflow): void {
    const reason = blocked(w);
    if (reason) app.say(reason);
    else void app.choose(w.key);
  }

  async function exportBackup(): Promise<void> {
    const path = await pickPath({
      save: true,
      title: "Export the backup",
      suggested: `${headset.codename}-${backup?.folder ?? "backup"}${BACKUP_SUFFIX}`,
      filters: [{ name: "overpressure backup", extensions: ["tar"] }],
    });
    if (path) await start("export", "backup_export", path);
  }

  async function importBackup(): Promise<void> {
    const path = await pickPath({ title: "Import a backup", filters: [{ name: "overpressure backup", extensions: ["tar"] }] });
    if (path) await start("import", "backup_import", path);
  }

  async function saveLogs(): Promise<void> {
    const path = await pickPath({
      save: true,
      title: "Save the logs",
      suggested: `overpressure-logs-${headset.codename}.zip`,
      filters: [{ name: "Zip", extensions: ["zip"] }],
    });
    if (path) await start("logs", "save_logs", path);
  }

  async function start(kind: "export" | "import" | "logs", request: string, path: string): Promise<void> {
    try {
      job = await app.startJob(kind, request, { codename: headset.codename, path });
    } catch (error) {
      app.say(error instanceof Error ? error.message : String(error));
    }
  }
</script>

<section class="hero">
  <div class="art"><HeadsetArt name={headset.name} width={200} /></div>
  <div class="info">
    <div class="name">
      <h1>{headset.name}</h1>
      {#if update}<Badge icon="up" tone="warn" tip={`Update available: ${update}`} />{/if}
      {#if headset.checkout}<Badge icon="branch" tone="info" tip="Development checkout: updated with git, never replaced" />{/if}
      {#if headset.pinned}<Badge icon="pin" tip="Kept at this release: updates leave it" />{/if}
    </div>
    <dl>
      <div><dt>Platform</dt><dd data-tip={headset.soc || undefined} tabindex="-1">{shortSoc(headset.soc)}</dd></div>
      <div><dt>Released</dt><dd>{headset.year || "—"}</dd></div>
      <div><dt>Version</dt><dd data-tip={headset.downloaded || undefined} tabindex="-1">{shortVersion(headset.downloaded)}</dd></div>
      <div><dt>Channel</dt><dd>{channelName(headset.channel) || "—"}</dd></div>
    </dl>
  </div>
  {#if !headset.checkout}
    <button class="btn secondary change" disabled={busy} onclick={() => (changing = true)}>Change version</button>
  {/if}
</section>

<h2 class="heading">Actions</h2>
<div class="cards">
  {#each headset.workflows as w (w.key)}
    {@const reason = blocked(w)}
    <div class="cell">
      <button
        class="tile tip-below"
        class:off={Boolean(reason)}
        aria-disabled={Boolean(reason)}
        aria-describedby="about-{w.key}"
        data-tip={w.summary}
        onclick={() => press(w)}
      >
        <span class="icon-box"><Icon name={ICONS[w.key] ?? "right"} size={24} /></span>
        <h3>{w.title}</h3>
        <span class="sr-only" id="about-{w.key}">{w.summary} {reason}</span>
      </button>
      <span class="badges">
        {#if w.problem}<Badge icon="lock" place="end" tip={w.problem} />{/if}
        {#if w.erases}<Badge icon="alert" tone="warn" place="end" tip="Erases the headset: its apps, games and data" />{/if}
      </span>
    </div>
  {:else}
    <p class="muted">No installer yet: Advanced mode has its steps.</p>
  {/each}
</div>

<div class="tools">
  {#if backup?.supported}
    <Badge
      icon="archive"
      tone={backup.present ? "good" : "warn"}
      tip={backup.present ? `Backup on this computer: ${backup.folder}` : "No backup on this computer yet: the install makes one"}
    />
    <button class="btn ghost" data-tip="Copy the backup into one file, to keep it somewhere else" disabled={busy || !backup.present} onclick={exportBackup}
      ><Icon name="upload" /> Export backup</button
    >
    <button class="btn ghost" data-tip="Bring an exported backup onto this computer" disabled={busy} onclick={importBackup}
      ><Icon name="download" /> Import backup</button
    >
  {/if}
  <button class="btn ghost" data-tip="The logs for a bug report in one file, this computer's identifiers masked" disabled={busy} onclick={saveLogs}
    ><Icon name="file" /> Save logs</button
  >
  {#if headset.has_steps}
    <button class="btn ghost" data-tip="Every step on its own, and the dependency checks" onclick={() => app.openAdvanced()}
      ><Icon name="sliders" /> Advanced</button
    >
  {/if}
</div>
{#if job}<div class="job"><JobStatus {job} /></div>{/if}

{#if changing}<AddDialog codename={headset.codename} onclose={() => (changing = false)} />{/if}

<style>
  .hero {
    display: flex;
    gap: 32px;
    align-items: center;
    padding: 8px 0 24px;
    border-bottom: 1px solid var(--line);
  }

  .art {
    width: 208px;
    flex-shrink: 0;
    display: grid;
    place-items: center;
  }

  .info {
    flex: 1;
    min-width: 0;
  }

  .name {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  h1 {
    font-size: 28px;
    margin-right: 4px;
  }

  dl {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px 20px;
    margin: 16px 0 0;
  }

  dt {
    font-size: 11px;
    color: var(--muted);
    margin-bottom: 3px;
  }

  dd {
    margin: 0;
    font-weight: 550;
    justify-self: start;
  }

  .change {
    align-self: flex-end;
  }

  .heading {
    margin: 24px 0 12px;
  }

  .cards {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 16px;
  }

  .cell {
    position: relative;
    display: grid;
  }

  .tile {
    aspect-ratio: 1.04;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 14px;
    padding: 16px;
    border-radius: 12px;
    border: 1px solid var(--line);
    background: var(--panel);
    transition:
      background 0.12s,
      border-color 0.12s;
  }

  .tile:hover:not(.off) {
    background: var(--hover);
    border-color: var(--blue);
  }

  .tile:hover:not(.off) .icon-box {
    color: var(--blue);
  }

  .tile.off {
    cursor: not-allowed;
  }

  .tile.off > :not(.sr-only) {
    opacity: 0.45;
  }

  /* A badge's own tooltip replaces the tile's while the pointer is on it. */
  :global(.cell:has(.badges:hover) .tile::after) {
    display: none;
  }

  .icon-box {
    width: 48px;
    height: 48px;
    border-radius: 12px;
    background: var(--surface);
    display: grid;
    place-items: center;
  }

  .badges {
    position: absolute;
    top: 10px;
    right: 10px;
    display: flex;
    gap: 4px;
  }

  .tools {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px;
    margin-top: 16px;
  }

  .tools :global(.badge) {
    margin-right: 4px;
  }

  .job {
    max-width: 420px;
    margin-top: 8px;
  }

  @media (max-width: 760px) {
    .cards {
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }

    .tile {
      aspect-ratio: auto;
      min-height: 120px;
    }
  }

  @media (max-width: 680px) {
    .hero {
      gap: 16px;
      flex-wrap: wrap;
    }

    .art {
      width: 130px;
    }
  }
</style>
