<!-- SPDX-License-Identifier: Unlicense -->
<!-- A workflow chosen after an earlier run of it: how far that run came (the run page's bar and marks), and
     "Continue with" the stage it stopped at, chosen; or, folded away, start over from a stage (its tasks run
     again; a stage that writes to the headset says so). -->
<script lang="ts">
  import { app, type Sheet } from "../lib/app.svelte";
  import { stageStatus } from "../lib/run";
  import Badge from "./Badge.svelte";
  import Icon from "./Icon.svelte";
  import StageBar from "./StageBar.svelte";

  let { sheet }: { sheet: Sheet } = $props();

  let dialog = $state<HTMLDialogElement>();
  /** "" continues; a stage's id starts over from it. */
  let from = $state("");
  let earlier = $state(false);

  const statuses = $derived(sheet.stages.map((s) => stageStatus(s)));
  const done = $derived(statuses.filter((s) => s === "done").length);
  const next = $derived(sheet.stages[statuses.findIndex((s) => s !== "done")]);
  const chosen = $derived(sheet.stages.find((s) => s.id === from));
  const bar = $derived(
    sheet.stages.map((s, i) => {
      const finished = s.tasks.filter((t) => t.status === "done" || t.status === "skipped").length;
      return { title: s.title, status: statuses[i] ?? "pending", fraction: s.tasks.length ? finished / s.tasks.length : 0 };
    }),
  );

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
    <h2 id="sheet-title">Continue {sheet.title}?</h2>
    <button class="iconbtn" aria-label="Close" onclick={close}><Icon name="close" /></button>
  </div>
  <div class="body">
    <div class="progress">
      <StageBar stages={bar} label="An earlier run did {done} of {sheet.stages.length} stages" />
      <p class="muted">An earlier run did {done} of {sheet.stages.length} stages.</p>
    </div>
    <fieldset>
      <legend class="sr-only">Where to start</legend>
      <label class="option continue">
        <input type="radio" name="from" value="" bind:group={from} />
        <span class="text"
          ><b>{next ? `Continue with “${next.title}”` : "Continue where it stopped"}</b><small
            >What is done stays done.</small
          ></span
        >
      </label>
      <details bind:open={earlier} ontoggle={() => !earlier && (from = "")}>
        <summary>Start over from a stage</summary>
        <div class="stages">
          {#each sheet.stages as s, i (s.id)}
            <label class="option">
              <input type="radio" name="from" value={s.id} bind:group={from} />
              <span class="mark {statuses[i]}"
                >{#if statuses[i] === "done"}<Icon name="check" size={12} />{:else if statuses[i] === "failed"}<Icon
                    name="alert"
                    size={12}
                  />{:else}{i + 1}{/if}</span
              >
              <span class="text">{s.title}</span>
              {#if s.tasks.some((t) => t.writes)}<Badge icon="alert" tone="warn" tip="Writes to the headset" focusable={false} />{/if}
            </label>
          {/each}
        </div>
      </details>
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
    gap: 14px;
  }

  .progress {
    display: grid;
    gap: 8px;
  }

  .progress p {
    font-size: 12px;
  }

  fieldset {
    border: 0;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 8px;
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
    padding: 12px;
  }

  .text {
    flex: 1;
    display: grid;
  }

  small {
    font-size: 12px;
    color: var(--muted);
  }

  summary {
    width: fit-content;
    padding: 4px 2px;
    color: var(--muted);
    font-size: 12px;
    cursor: pointer;
  }

  summary:hover {
    color: var(--blue);
  }

  .stages {
    display: grid;
    gap: 4px;
    margin-top: 6px;
  }

  .mark {
    width: 22px;
    height: 22px;
    flex-shrink: 0;
    border-radius: 50%;
    border: 2px solid var(--track);
    display: grid;
    place-items: center;
    color: var(--muted);
    font-size: 11px;
    font-weight: 650;
  }

  .mark.done {
    background: var(--green);
    border-color: var(--green);
    color: var(--bg);
  }

  .mark.failed {
    background: var(--bad-bg);
    border-color: var(--red);
    color: var(--red);
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
