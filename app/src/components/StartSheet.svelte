<!-- SPDX-License-Identifier: Unlicense -->
<!-- A workflow chosen after an earlier run of it: continue where it stopped (the first option, chosen), or start
     over from a stage (its tasks run again; a stage that writes to the headset says so). -->
<script lang="ts">
  import { app, type Sheet } from "../lib/app.svelte";
  import Badge from "./Badge.svelte";
  import Icon from "./Icon.svelte";

  let { sheet }: { sheet: Sheet } = $props();

  let dialog = $state<HTMLDialogElement>();
  /** "" continues; a stage's id starts over from it. */
  let from = $state("");

  const finished = (s: Sheet["stages"][number]) => s.tasks.every((t) => t.status === "done" || t.status === "skipped");
  const done = $derived(sheet.stages.filter(finished).length);
  const chosen = $derived(sheet.stages.find((s) => s.id === from));

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
    <p class="muted">An earlier run did {done} of {sheet.stages.length} stages.</p>
    <fieldset>
      <legend class="sr-only">Where to start</legend>
      <label class="option continue">
        <input type="radio" name="from" value="" bind:group={from} />
        <span class="text"><b>Continue where it stopped</b><small>What is done stays done.</small></span>
      </label>
      <p class="over muted">Or start over from a stage:</p>
      {#each sheet.stages as s (s.id)}
        <label class="option">
          <input type="radio" name="from" value={s.id} bind:group={from} />
          <span class="mark" class:done={finished(s)}>{#if finished(s)}<Icon name="check" size={12} />{/if}</span>
          <span class="text">{s.title}</span>
          {#if s.tasks.some((t) => t.writes)}<Badge icon="alert" tone="warn" tip="Writes to the headset" focusable={false} />{/if}
        </label>
      {/each}
    </fieldset>
    {#if chosen}
      <p class="callout info">From “{chosen.title}” on, every task runs again, also those an earlier run finished.</p>
    {/if}
  </div>
  <div class="foot">
    <button class="btn ghost" onclick={close}>Not now</button>
    <button class="btn primary" onclick={() => app.begin(sheet.workflow, chosen?.id ?? "")}
      >{chosen ? "Start over" : "Continue"}</button
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

  fieldset {
    border: 0;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 4px;
  }

  .option {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 10px;
    border-radius: 8px;
    border: 1px solid var(--line);
    cursor: pointer;
  }

  .option:hover,
  .option:has(input:checked) {
    border-color: var(--blue);
    background: var(--selected);
  }

  .option input {
    margin: 0;
    accent-color: var(--blue);
  }

  .continue {
    padding: 10px;
  }

  .text {
    flex: 1;
    display: grid;
  }

  small {
    font-size: 12px;
    color: var(--muted);
  }

  .over {
    margin: 8px 0 2px;
    font-size: 12px;
  }

  .mark {
    width: 20px;
    height: 20px;
    flex-shrink: 0;
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
