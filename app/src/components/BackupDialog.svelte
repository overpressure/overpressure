<!-- SPDX-License-Identifier: Unlicense -->
<!-- The headset's backup on this computer: whether there is one, its export into one file to keep somewhere
     else, and an exported one brought back onto this computer. -->
<script lang="ts">
  import { app } from "../lib/app.svelte";
  import { pickPath } from "../lib/bridge";
  import type { JobState } from "../lib/job";
  import type { Backup, HeadsetEntry } from "../lib/protocol";
  import Icon from "./Icon.svelte";
  import Spinner from "./Spinner.svelte";
  import JobStatus from "./JobStatus.svelte";

  let { headset, onclose }: { headset: HeadsetEntry; onclose: () => void } = $props();

  const SUFFIX = ".overpressure-backup.tar";
  const FILTERS = [{ name: "overpressure backup", extensions: ["tar"] }];

  let dialog = $state<HTMLDialogElement>();
  let backup = $state<Backup | null>(null);
  let job = $state<JobState | null>(null);

  const busy = $derived(app.running || app.jobRunning);

  async function read(): Promise<void> {
    backup = await app.engine.request<Backup>("backup", { codename: headset.codename });
  }

  $effect(() => {
    dialog?.showModal();
    void read();
  });

  $effect(() => {
    if (job?.code === 0) void read();
  });

  async function exportBackup(): Promise<void> {
    const path = await pickPath({
      save: true,
      title: "Export the backup",
      suggested: `${headset.codename}-${backup?.folder ?? "backup"}${SUFFIX}`,
      filters: FILTERS,
    });
    if (path) await start("export", "backup_export", path);
  }

  async function importBackup(): Promise<void> {
    const path = await pickPath({ title: "Import a backup", filters: FILTERS });
    if (path) await start("import", "backup_import", path);
  }

  async function start(kind: "export" | "import", request: string, path: string): Promise<void> {
    try {
      job = await app.startJob(kind, request, { codename: headset.codename, path });
    } catch (error) {
      app.say(error instanceof Error ? error.message : String(error));
    }
  }

  function close(): void {
    dialog?.close();
    onclose();
  }
</script>

<dialog bind:this={dialog} aria-labelledby="backup-title" {onclose}>
  <div class="title">
    <h2 id="backup-title">Backup</h2>
    <button class="iconbtn" aria-label="Close" onclick={close}><Icon name="close" /></button>
  </div>
  <div class="body">
    {#if backup === null}
      <p class="muted wait" role="status"><Spinner />Looking for it…</p>
    {:else if backup.present}
      <div class="state good">
        <Icon name="check" />
        <div><b>On this computer</b><small data-tip={backup.folder} tabindex="-1">{backup.folder}</small></div>
      </div>
    {:else}
      <div class="state warn">
        <Icon name="alert" />
        <div><b>None on this computer yet</b><small>The install makes one before it changes anything.</small></div>
      </div>
    {/if}
    {#if job}<JobStatus {job} />{/if}
  </div>
  <div class="foot">
    <button
      class="btn secondary"
      data-tip="Copy the backup into one file, to keep it somewhere else"
      disabled={busy || !backup?.present}
      onclick={exportBackup}><Icon name="upload" /> Export…</button
    >
    <button
      class="btn secondary"
      data-tip="Bring an exported backup onto this computer"
      disabled={busy}
      onclick={importBackup}><Icon name="download" /> Import…</button
    >
  </div>
</dialog>

<style>
  .title {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 16px 20px 8px;
  }

  .body {
    padding: 0 20px 16px;
    display: grid;
    gap: 12px;
  }

  .state {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    padding: 12px;
    border-radius: 10px;
    border: 1px solid var(--line);
  }

  .state.good {
    color: var(--good-text);
    background: var(--good-bg);
    border-color: transparent;
  }

  .state.warn {
    color: var(--warn-text);
    background: var(--warn-bg);
    border-color: var(--warn-line);
  }

  .state div {
    display: grid;
    min-width: 0;
  }

  .state b {
    color: var(--text);
  }

  small {
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .foot {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 20px;
    border-top: 1px solid var(--line);
  }
  .wait {
    display: flex;
    align-items: center;
    gap: 8px;
  }
</style>
