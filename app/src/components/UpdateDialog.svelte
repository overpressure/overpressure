<!-- SPDX-License-Identifier: Unlicense -->
<!-- A newer overpressure: its notes, and Update and restart (the installed app replaces itself: the download is
     checked against the key it was built with, the engine ends, the app restarts) or, for overpressure from a
     folder, its release's page. Not while a run or a job is under way. -->
<script lang="ts">
  import { app } from "../lib/app.svelte";
  import { installUpdate } from "../lib/bridge";
  import type { AppUpdate } from "../lib/protocol";
  import Icon from "./Icon.svelte";
  import Spinner from "./Spinner.svelte";

  let { update, installs }: { update: AppUpdate; installs: boolean } = $props();

  let dialog = $state<HTMLDialogElement>();
  let working = $state(false);
  let done = $state(0);
  let total = $state<number | null>(null);
  let failure = $state("");
  const busy = $derived(app.running || app.jobRunning);
  const mb = (bytes: number): string => (bytes / 1e6).toFixed(1);
  const line = $derived(
    total ? `Downloading… ${mb(done)} of ${mb(total)} MB` : done ? `Downloading… ${mb(done)} MB` : "Downloading…",
  );

  $effect(() => {
    dialog?.showModal();
  });

  async function install(): Promise<void> {
    working = true;
    failure = "";
    done = 0;
    total = null;
    try {
      if (!update.manifest) {
        // The demo: nothing downloads.
        for (total = 30e6; done < total; done += 6e6) await new Promise((r) => setTimeout(r, 250));
        app.say("Demo: nothing was installed.");
        close();
        return;
      }
      await installUpdate(update.manifest, update.semver, (d, t) => {
        done = d;
        total = t;
      });
    } catch (error) {
      failure = error instanceof Error ? error.message : String(error);
    } finally {
      working = false;
    }
  }

  function close(): void {
    dialog?.close();
  }
</script>

<dialog
  bind:this={dialog}
  aria-labelledby="update-title"
  oncancel={(e) => working && e.preventDefault()}
  onclose={() => (app.updateOpen = false)}
>
  <div class="title">
    <h2 id="update-title">overpressure {update.words} is out</h2>
    <button class="iconbtn" aria-label="Later" disabled={working} onclick={close}><Icon name="close" /></button>
  </div>
  <div class="body">
    {#if update.notes}<pre class="notes">{update.notes}</pre>{/if}
    {#if update.page}<a href={update.page} target="_blank" rel="noreferrer">Everything in this release</a>{/if}
    {#if working && total && done >= total}
      <p class="muted wait" role="status"><Spinner />Installing… overpressure restarts when it is done.</p>
    {:else if working}
      <div class="progress" role="status">
        <div class="bar" class:unknown={!total}><span style:width="{total ? (done / total) * 100 : 30}%"></span></div>
        <p>{line}</p>
      </div>
    {:else if busy}
      <p class="callout"><Icon name="info" /> Let what runs finish first: the update restarts overpressure.</p>
    {:else if installs}
      <p class="muted">overpressure restarts with the new version. Your data stays where it is.</p>
    {:else}
      <p class="muted">This overpressure runs from its folder: download the new release from its page.</p>
    {/if}
    {#if failure}<p class="callout bad"><Icon name="alert" /> {failure}</p>{/if}
  </div>
  <div class="foot">
    <button class="btn ghost" disabled={working} onclick={close}>Later</button>
    {#if installs}
      <button class="btn primary" disabled={working || busy} onclick={install}
        ><Icon name="download" /> {working ? "Updating…" : "Update and restart"}</button
      >
    {:else if update.page}
      <a class="btn primary" href={update.page} target="_blank" rel="noreferrer">Open its page</a>
    {/if}
  </div>
</dialog>

<style>
  .title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 20px 8px;
  }

  .body {
    padding: 0 20px 16px;
    display: grid;
    gap: 10px;
    font-size: 13px;
  }

  .notes {
    max-height: 240px;
    overflow: auto;
    margin: 0;
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: 8px;
    font: inherit;
    font-size: 12px;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .progress {
    display: grid;
    gap: 6px;
  }

  .progress p {
    font-size: 12px;
    color: var(--muted);
  }

  .bar {
    height: 6px;
    border-radius: 3px;
    background: var(--track);
    overflow: hidden;
  }

  .bar span {
    display: block;
    height: 100%;
    border-radius: 3px;
    background: var(--blue);
    transition: width 0.3s ease;
  }

  .bar.unknown span {
    animation: fro 1.4s ease-in-out infinite alternate;
  }

  @keyframes fro {
    from {
      transform: translateX(0);
    }
    to {
      transform: translateX(233%);
    }
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
