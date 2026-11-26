<!-- SPDX-License-Identifier: Unlicense -->
<!-- A workflow chosen after an earlier run of it: continue where it stopped, or start over from a stage (its
     tasks run again; a stage that writes to the headset says so). -->
<script lang="ts">
  import { app, type Sheet } from "../lib/app.svelte";
  import Badge from "./Badge.svelte";
  import Icon from "./Icon.svelte";

  let { sheet }: { sheet: Sheet } = $props();

  let dialog = $state<HTMLDialogElement>();
  let over = $state("");

  const finished = (s: Sheet["stages"][number]) => s.tasks.every((t) => t.status === "done" || t.status === "skipped");
  const done = $derived(sheet.stages.filter(finished).length);
  const chosen = $derived(sheet.stages.find((s) => s.id === over));

  $effect(() => {
    dialog?.showModal();
  });

  function close(): void {
    dialog?.close();
    app.sheet = null;
  }
</script>

<dialog bind:this={dialog} aria-labelledby="sheet-title" onclose={() => (app.sheet = null)}>
  <div class="title">
    <h2 id="sheet-title">{sheet.title}: welcome back</h2>
    <button class="iconbtn" aria-label="Close" onclick={close}><Icon name="close" /></button>
  </div>
  <div class="body">
    <p class="muted">An earlier run did {done} of {sheet.stages.length} stages. Continue where it stopped, or start over from a stage.</p>
    <ol>
      {#each sheet.stages as s (s.id)}
        <li>
          <button class:chosen={over === s.id} onclick={() => (over = over === s.id ? "" : s.id)}>
            <span class="mark" class:done={finished(s)}>{#if finished(s)}<Icon name="check" size={12} />{/if}</span>
            <span class="name">{s.title}</span>
            {#if s.tasks.some((t) => t.writes)}<Badge icon="alert" tone="warn" place="end" tip="Writes to the headset" focusable={false} />{/if}
          </button>
        </li>
      {/each}
    </ol>
    {#if chosen}
      <p class="callout info">From “{chosen.title}” on, every task runs again, also those an earlier run finished.</p>
    {/if}
  </div>
  <div class="foot">
    {#if chosen}
      <button class="btn ghost" onclick={() => (over = "")}>Cancel</button>
      <button class="btn primary" onclick={() => app.begin(sheet.workflow, chosen.id)}>Start over from here</button>
    {:else}
      <button class="btn ghost" onclick={close}>Not now</button>
      <button class="btn primary" onclick={() => app.begin(sheet.workflow)}>Continue</button>
    {/if}
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

  ol {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 4px;
  }

  ol button {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 10px;
    border-radius: 8px;
    border: 1px solid var(--line);
    text-align: left;
  }

  ol button:hover,
  ol button.chosen {
    border-color: var(--blue);
    background: var(--selected);
  }

  .name {
    flex: 1;
  }

  .mark {
    width: 20px;
    height: 20px;
    border-radius: 50%;
    border: 1px solid var(--line);
    display: grid;
    place-items: center;
  }

  .mark.done {
    background: var(--good-bg);
    color: var(--good-text);
    border-color: var(--good-text);
  }

  .callout {
    font-size: 12px;
  }

  .foot {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 20px;
    border-top: 1px solid var(--line);
  }
</style>
