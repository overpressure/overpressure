<!-- SPDX-License-Identifier: Unlicense -->
<!-- A run in two columns: on the left the ring with its stages' names; on the right where it stands, the active
     stage's tasks and notes, Stop (at once, or after a write under way: never cuts one off) and Details (the log).
     The engine's questions come as dialogs. Its end: the headset's own words and next steps, or why it failed and
     what to do (Retry, Import backup). The top bar names the run. -->
<script lang="ts">
  import { tick } from "svelte";
  import Badge from "../components/Badge.svelte";
  import Icon from "../components/Icon.svelte";
  import JobStatus from "../components/JobStatus.svelte";
  import Ring from "../components/Ring.svelte";
  import { app } from "../lib/app.svelte";
  import { pickPath } from "../lib/bridge";
  import type { JobState } from "../lib/job";
  import type { FinishedPage } from "../lib/protocol";
  import type { RunState } from "../lib/run";
  import { overall, stageStatus } from "../lib/run";

  const run = $derived(app.run as RunState);

  const STOP_NOW = "It stops now; nothing on the headset changes. Starting again picks up here.";
  const STOP_AFTER_WRITE = "This step writes to the headset and finishes first; the run stops right after.";

  const now = $derived(overall(run));
  const statuses = $derived(run.stages.map(stageStatus));
  const ringStages = $derived(run.stages.map((s, i) => ({ title: s.title, status: statuses[i] ?? "pending" })));
  const stage = $derived(run.stages[now.stage]);
  const outcome = $derived(run.finished?.status ?? null);
  const writing = $derived(run.stages.some((s) => s.tasks.some((t) => t.status === "running" && t.writes)));

  let details = $state(false);
  let stopping = $state(false);
  let values = $state<Record<string, string>>({});
  let askDialog = $state<HTMLDialogElement>();
  let stopDialog = $state<HTMLDialogElement>();
  let log = $state<HTMLPreElement>();
  let page = $state<FinishedPage | null>(null);
  let importJob = $state<JobState | null>(null);

  $effect(() => {
    if (outcome === "done" && page === null) {
      void app.engine
        .request<FinishedPage>("finished_page", { codename: app.codename, workflow: run.workflow })
        .then((found) => (page = found));
    }
  });

  function act(action: FinishedPage["actions"][number]): void {
    app.run = null;
    if (action.workflow) void app.choose(action.workflow);
    else app.openAdvanced(action.step);
  }

  async function importBackup(): Promise<void> {
    const path = await pickPath({ title: "Import a backup", filters: [{ name: "overpressure backup", extensions: ["tar"] }] });
    if (!path) return;
    try {
      importJob = await app.startJob("import", "backup_import", { codename: app.codename, path });
    } catch (error) {
      app.say(error instanceof Error ? error.message : String(error));
    }
  }

  $effect(() => {
    if (run.ask && askDialog && !askDialog.open) {
      values = Object.fromEntries((run.ask.fields ?? []).map((f) => [f.name, ""]));
      askDialog.showModal();
    } else if (!run.ask && askDialog?.open) askDialog.close();
  });

  $effect(() => {
    void run.log.length;
    if (details && log) void tick().then(() => log && (log.scrollTop = log.scrollHeight));
  });

  async function answer(accepted: boolean): Promise<void> {
    const ask = run.ask;
    if (!ask) return;
    const reply = accepted ? values : {};
    run.ask = null;
    values = {};
    askDialog?.close();
    await app.engine.request("answer", { run: run.run, answer: ask.id, accepted, values: reply });
  }

  async function stop(): Promise<void> {
    stopDialog?.close();
    stopping = true;
    await app.engine.request("stop", { run: run.run });
  }
</script>

<div class="run">
  <div class="left">
    <Ring stages={ringStages} fraction={now.fraction} active={now.stage} {outcome} />
  </div>

  <div class="right">
    <div class="where">
      {#if outcome === "done"}<span class="pill good">Completed</span>
      {:else if outcome === "failed"}<span class="pill bad">Failed</span>
      {:else if outcome === "stopped"}<span class="pill">Stopped</span>
      {:else if stopping}<span class="pill warn">Stopping</span>
      {:else}<span class="pill info">In progress</span>{/if}
      <span class="muted" data-tip="Unplugging it now can leave it half done">{run.headset}: keep it connected</span>
    </div>

    {#if outcome === "done" && page}
      <section class="end">
        <h2>{page.title}</h2>
        <p class="muted">{page.text}</p>
        {#if page.actions.length}
          <div class="next">
            {#each page.actions as action (action.label)}
              <button class="btn secondary" onclick={() => act(action)}>{action.label}</button>
            {/each}
          </div>
        {/if}
      </section>
    {:else if run.finished && run.finished.status !== "done"}
      <div class="callout" class:bad={run.finished.status === "failed"} class:info={run.finished.status === "stopped"}>
        <Icon name={run.finished.status === "failed" ? "alert" : "info"} />
        <div>
          {#if run.finished.error}<h3>{run.finished.error}</h3>{/if}
          {#if run.finished.where}<p>{run.finished.where}</p>{/if}
          {#if run.finished.offer === "import-backup"}
            <div class="offer">
              {#if importJob}<JobStatus job={importJob} />{/if}
              <button class="btn secondary" disabled={app.jobRunning} onclick={importBackup}>Import backup…</button>
            </div>
          {/if}
        </div>
      </div>
    {:else if stage && !outcome}
      <section class="tasks" aria-label="{stage.title}: tasks">
        <h2>{stage.title}</h2>
        {#each stage.tasks as t (t.id)}
          <div class="task {t.status}">
            <span class="mark">
              {#if t.status === "done" || t.status === "skipped"}<Icon name="check" size={12} />
              {:else if t.status === "failed"}<Icon name="alert" size={12} />{/if}
            </span>
            <span class="name">{t.title}</span>
            {#if t.writes}<Badge icon="alert" tone="warn" tip="Writes to the headset" />{/if}
            <span class="state muted">
              {#if t.status === "running"}{t.progress || "Working…"}{:else if t.status === "skipped"}already there{:else if t.status === "done"}done{:else}{t.estimate}{/if}
            </span>
          </div>
        {/each}
      </section>
    {/if}

    {#each run.notes.slice(-3) as note, i (i)}
      <div class="callout note" class:info={note.level === "info"}><Icon name="info" /><p>{note.text}</p></div>
    {/each}

    <div class="footer">
      <button class="btn ghost" aria-expanded={details} onclick={() => (details = !details)}
        ><Icon name="terminal" /> {details ? "Hide details" : "Details"}</button
      >
      <div class="buttons">
        {#if !run.finished}
          <button class="btn secondary" disabled={stopping} onclick={() => stopDialog?.showModal()}
            ><Icon name="stop" /> Stop</button
          >
        {:else if run.finished.status === "done"}
          <button class="btn primary" onclick={() => app.leaveRun()}>Done <Icon name="check" /></button>
        {:else}
          <button class="btn secondary" onclick={() => app.leaveRun()}>Back</button>
          <button class="btn primary" onclick={() => app.begin(run.workflow)}
            >{run.finished.status === "failed" ? "Retry" : "Start again"}</button
          >
        {/if}
      </div>
    </div>
  </div>
</div>

{#if details}
  <pre class="log" bind:this={log}>{run.log.slice(-400).join("\n")}</pre>
{/if}

<dialog bind:this={askDialog} aria-labelledby="ask-title" oncancel={(e) => e.preventDefault()}>
  {#if run.ask}
    <form
      method="dialog"
      onsubmit={(e) => {
        e.preventDefault();
        void answer(true);
      }}
    >
      <div class="dtitle"><h2 id="ask-title">{run.ask.title}</h2></div>
      <div class="dbody">
        <p>{run.ask.text}</p>
        {#each run.ask.fields ?? [] as f (f.name)}
          <label class="field"
            >{f.label}{f.optional ? " (optional)" : ""}
            <input
              type={f.secret ? "password" : "text"}
              autocomplete="off"
              required={!f.optional}
              bind:value={values[f.name]}
            /></label
          >
        {/each}
      </div>
      <div class="dfoot">
        <button type="button" class="btn secondary" onclick={() => answer(false)}>{run.ask.decline}</button>
        <button type="submit" class="btn primary">{run.ask.accept}</button>
      </div>
    </form>
  {/if}
</dialog>

<dialog bind:this={stopDialog} aria-labelledby="stop-title">
  <div class="dtitle"><h2 id="stop-title">Stop the run?</h2></div>
  <p class="dbody">{writing ? STOP_AFTER_WRITE : STOP_NOW}</p>
  <div class="dfoot">
    <button class="btn secondary" onclick={() => stopDialog?.close()}>Keep going</button>
    <button class="btn primary" onclick={stop}>Stop</button>
  </div>
</dialog>

<style>
  .run {
    display: grid;
    grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr);
    gap: 24px;
    align-items: center;
  }

  .right {
    display: grid;
    gap: 14px;
    align-content: center;
    min-width: 0;
  }

  .where {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 12px;
  }

  .tasks h2 {
    font-size: 14px;
    padding: 8px 14px 4px;
  }

  .mark {
    width: 23px;
    height: 23px;
    flex-shrink: 0;
    border-radius: 50%;
    display: grid;
    place-items: center;
    font-size: 10px;
    border: 1px solid var(--line);
    color: var(--muted);
  }

  .done .mark {
    background: var(--good-bg);
    color: var(--good-text);
    border-color: var(--good-text);
  }

  .active .mark,
  .running .mark {
    background: var(--blue-bg);
    color: var(--blue);
    border-color: var(--blue);
  }

  .failed .mark {
    background: var(--bad-bg);
    color: var(--red);
    border-color: var(--red);
  }

  .tasks {
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--panel);
    padding: 4px 0;
  }

  .task {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 7px 14px;
  }

  .task .mark {
    width: 18px;
    height: 18px;
  }

  .task .name {
    font-weight: 550;
  }

  .task.pending .name {
    color: var(--muted);
  }

  .task .state {
    margin-left: auto;
    font-size: 12px;
    font-variant-numeric: tabular-nums;
    text-align: right;
  }

  .end {
    padding: 16px;
    border-radius: 10px;
    border: 1px solid var(--line);
    background: var(--panel);
    display: grid;
    gap: 6px;
  }

  .next {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 8px;
  }

  .offer {
    display: grid;
    gap: 8px;
    justify-items: start;
    margin-top: 10px;
  }

  .note {
    align-items: center;
  }

  .footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
  }

  .buttons {
    display: flex;
    gap: 8px;
  }

  .log {
    margin: 16px 0 0;
    max-height: 260px;
    overflow: auto;
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid var(--line);
    background: var(--inset);
    font: 12px/1.5 ui-monospace, "Cascadia Mono", Menlo, monospace;
    white-space: pre-wrap;
  }

  .dtitle {
    padding: 16px 20px 8px;
  }

  .dbody {
    padding: 0 20px 16px;
    display: grid;
    gap: 12px;
  }

  .field {
    display: grid;
    gap: 5px;
    font-size: 12px;
    color: var(--muted);
  }

  .field input {
    padding: 7px 9px;
    border-radius: 6px;
    border: 1px solid var(--line);
    background: var(--inset);
    color: var(--text);
  }

  @media (max-width: 860px) {
    .run {
      grid-template-columns: minmax(0, 1fr);
      gap: 8px;
    }
  }

  .dfoot {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 20px;
    border-top: 1px solid var(--line);
  }
</style>
