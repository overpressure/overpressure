<!-- SPDX-License-Identifier: Unlicense -->
<!-- The window closed while something runs: stop it and close, or keep it running. Closing ends the engine's stdin, so
     a task that writes to the headset finishes its write first; another click on the window's × closes too. -->
<script lang="ts">
  import { app } from "../lib/app.svelte";
  import { closeWindow, keepWindow } from "../lib/bridge";
  import { runTitle } from "../lib/words";
  import Icon from "./Icon.svelte";

  let { onclose }: { onclose: () => void } = $props();

  let dialog = $state<HTMLDialogElement>();

  const JOBS: Record<string, string> = {
    carry: "Bringing your earlier data over",
    download: "A download",
    export: "The backup's export",
    import: "The backup's import",
    logs: "Saving the logs",
    step: "A step",
  };
  const what = $derived(
    app.running && app.run
      ? `${runTitle(app.run.workflow)} is running.`
      : `${JOBS[app.job ? (app.jobs[app.job]?.kind ?? "step") : "step"]} is running.`,
  );
  const touches = $derived(app.running || (app.job !== null && app.jobs[app.job]?.kind === "step"));

  $effect(() => {
    dialog?.showModal();
  });

  /** The dialog closed without closing the window (its buttons, Escape): what runs goes on. */
  function keep(): void {
    void keepWindow();
    onclose();
  }
</script>

<dialog bind:this={dialog} aria-labelledby="close-title" onclose={keep}>
  <div class="title">
    <h2 id="close-title">Close overpressure?</h2>
    <button class="iconbtn" aria-label="Keep it running" onclick={() => dialog?.close()}><Icon name="close" /></button>
  </div>
  <div class="body">
    <p>{what} Closing stops it.</p>
    {#if touches}
      <p class="callout">
        A task that writes to your headset finishes its write first, then it stops: the next start goes on where it
        stopped. Keep the cable in until the window has closed.
      </p>
    {/if}
  </div>
  <div class="foot">
    <button class="btn ghost" onclick={() => void closeWindow()}>Stop and close</button>
    <button class="btn primary" onclick={() => dialog?.close()}>Keep it running</button>
  </div>
</dialog>
