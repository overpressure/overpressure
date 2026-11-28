<!-- SPDX-License-Identifier: Unlicense -->
<!-- A run, read from the top: the status card says what happens now in words, how far the run is (one bar split
     into its stages) and how long it still takes, with Stop (at once, or after a write under way: never cuts one
     off). When the engine needs the owner, "Your turn" comes right under it, in the page, so the run stays in
     view. Below, every stage in order: the one under way open with its tasks and notes. At the end the status
     card turns into the result: the headset's own words and next steps, or why it failed and what to do (Retry,
     Import backup), or that it stopped. The log is folded away at the bottom. -->
<script lang="ts">
  import { tick } from "svelte";
  import Icon from "../components/Icon.svelte";
  import JobStatus from "../components/JobStatus.svelte";
  import Spinner from "../components/Spinner.svelte";
  import StageBar from "../components/StageBar.svelte";
  import StageList from "../components/StageList.svelte";
  import { app } from "../lib/app.svelte";
  import { pickPath } from "../lib/bridge";
  import type { JobState } from "../lib/job";
  import type { FinishedPage } from "../lib/protocol";
  import type { RunState } from "../lib/run";
  import { duration, overall, runLeft, stageFraction, stageStatus } from "../lib/run";

  const run = $derived(app.run as RunState);

  const STOP_NOW = "It stops now; nothing on the headset changes. Starting again picks up here.";
  const STOP_AFTER_WRITE = "This step writes to the headset and finishes first; the run stops right after.";

  const now = $derived(overall(run));
  const stage = $derived(run.stages[now.stage]);
  const outcome = $derived(run.finished?.status ?? null);
  const writing = $derived(run.stages.some((s) => s.tasks.some((t) => t.status === "running" && t.writes)));
  const bar = $derived(
    run.stages.map((s) => ({ title: s.title, status: stageStatus(s), fraction: stageFraction(s) })),
  );
  const percent = $derived(Math.min(outcome === "done" ? 100 : 99, Math.round(now.fraction * 100)));
  const left = $derived(duration(runLeft(run)));
  const running = $derived(now.task?.status === "running" ? now.task : undefined);
  const headline = $derived(run.ask ? "Waiting for you" : (running?.title ?? stage?.title ?? "Starting"));
  const taskIds = $derived(new Set(run.stages.flatMap((s) => s.tasks.map((t) => t.id))));
  /** Notes of no task in the plan: they go under the status card. */
  const loose = $derived(run.notes.filter((n) => !taskIds.has(n.task)).slice(-3));

  let details = $state(false);
  let stopping = $state(false);
  let copied = $state(false);
  let values = $state<Record<string, string>>({});
  let turn = $state<HTMLFormElement>();
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

  // The engine asks: its card comes into view, its first field (or its answer) takes the keyboard.
  let asked = "";
  $effect(() => {
    const ask = run.ask;
    if (!ask || ask.id === asked) return;
    asked = ask.id;
    values = Object.fromEntries((ask.fields ?? []).map((f) => [f.name, ""]));
    void tick().then(() => {
      turn?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      turn?.querySelector<HTMLElement>("input, button[type=submit]")?.focus({ preventScroll: true });
    });
  });

  $effect(() => {
    void run.log.length;
    if (details && log) void tick().then(() => log && (log.scrollTop = log.scrollHeight));
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

  async function answer(accepted: boolean): Promise<void> {
    const ask = run.ask;
    if (!ask) return;
    const reply = accepted ? values : {};
    run.ask = null;
    values = {};
    await app.engine.request("answer", { run: run.run, answer: ask.id, accepted, values: reply });
  }

  async function stop(): Promise<void> {
    stopDialog?.close();
    stopping = true;
    await app.engine.request("stop", { run: run.run });
  }

  async function copyLog(): Promise<void> {
    try {
      await navigator.clipboard.writeText(run.log.join("\n"));
      copied = true;
      setTimeout(() => (copied = false), 1500);
    } catch {
      app.say("The log could not be copied.");
    }
  }
</script>

<div class="run">
  <section
    class="status"
    class:good={outcome === "done"}
    class:bad={outcome === "failed"}
    class:ended={outcome !== null}
    aria-live="polite"
  >
    {#if !run.finished}
      <div class="top">
        <div class="now">
          <p class="eyebrow">
            {#if run.stages.length}Stage {now.stage + 1} of {run.stages.length} · {stage?.title}{:else}Preparing{/if}
          </p>
          <h2>
            {#if stopping}<Spinner size={16} /> Stopping…{:else}{headline}{/if}
          </h2>
          {#if stopping}
            <p class="detail">{writing ? "A write under way finishes first." : "It stops at once."}</p>
          {:else if run.ask}
            <p class="detail">{run.ask.title}: see below.</p>
          {:else if running?.progress}
            <p class="detail">{running.progress}</p>
          {/if}
        </div>
        <button class="btn secondary stop" disabled={stopping} onclick={() => stopDialog?.showModal()}
          ><Icon name="stop" /> Stop</button
        >
      </div>
      <StageBar
        stages={bar}
        label="{percent}% done, stage {now.stage + 1} of {run.stages.length}{left ? `, ${left} left` : ''}"
      />
      <div class="facts">
        <span class="percent">{percent}%</span>
        {#if left}<span>{left} left</span>{/if}
        <span class="connected"><Icon name="plug" size={14} /> Keep the {run.headset || "headset"} connected</span>
      </div>
    {:else if outcome === "done"}
      <div class="result">
        <span class="sign good"><Icon name="check" size={22} /></span>
        <div class="words">
          <h2>{page?.title ?? "Done"}</h2>
          {#if page?.text}<p>{page.text}</p>{/if}
        </div>
      </div>
      <div class="actions">
        {#if page?.actions.length}
          <div class="next" role="group" aria-label="Next steps">
            {#each page.actions as action (action.label)}
              <button class="btn secondary" onclick={() => act(action)}>{action.label}</button>
            {/each}
          </div>
        {/if}
        <button class="btn primary" onclick={() => app.leaveRun()}>Done</button>
      </div>
    {:else if outcome === "failed"}
      <div class="result">
        <span class="sign bad"><Icon name="alert" size={22} /></span>
        <div class="words">
          <h2>{run.finished.error || "It did not finish"}</h2>
          {#if run.finished.where}<p>{run.finished.where}</p>{/if}
        </div>
      </div>
      {#if importJob}<JobStatus job={importJob} />{/if}
      <div class="actions">
        <button class="btn ghost" onclick={() => app.leaveRun()}>Back</button>
        {#if run.finished.offer === "import-backup"}
          <button class="btn secondary" disabled={app.jobRunning} onclick={importBackup}
            ><Icon name="upload" /> Import backup…</button
          >
        {/if}
        <button class="btn primary" onclick={() => app.begin(run.workflow)}><Icon name="refresh" /> Retry</button>
      </div>
    {:else}
      <div class="result">
        <span class="sign"><Icon name="stop" size={20} /></span>
        <div class="words">
          <h2>Stopped</h2>
          <p>{run.finished.where || "Nothing is lost: starting again picks up where it stopped."}</p>
        </div>
      </div>
      <div class="actions">
        <button class="btn ghost" onclick={() => app.leaveRun()}>Back</button>
        <button class="btn primary" onclick={() => app.begin(run.workflow)}>Start again</button>
      </div>
    {/if}

    {#each loose as note, i (i)}
      <p class="loose" class:warning={note.level === "warning"}>
        <Icon name={note.level === "warning" ? "alert" : "info"} size={14} />{note.text}
      </p>
    {/each}
  </section>

  {#if run.ask}
    <form
      class="turn"
      bind:this={turn}
      aria-labelledby="turn-title"
      onsubmit={(e) => {
        e.preventDefault();
        void answer(true);
      }}
    >
      <p class="eyebrow"><Icon name="info" size={14} /> Your turn</p>
      <h3 id="turn-title">{run.ask.title}</h3>
      <p class="ask-text">{run.ask.text}</p>
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
      <div class="actions">
        <button type="button" class="btn ghost" onclick={() => answer(false)}>{run.ask.decline}</button>
        <button type="submit" class="btn primary">{run.ask.accept}</button>
      </div>
    </form>
  {/if}

  {#if run.stages.length}
    <section class="list" aria-label="Stages">
      <StageList {run} active={now.stage} />
    </section>
  {:else}
    <p class="muted wait"><Spinner /> Reading the plan…</p>
  {/if}

  <details class="log" bind:open={details}>
    <summary><Icon name="terminal" size={14} /> Log <span class="count">{run.log.length} lines</span></summary>
    <div class="logbody">
      <button class="btn ghost copy" onclick={copyLog}><Icon name="copy" size={14} /> {copied ? "Copied" : "Copy"}</button>
      <pre bind:this={log}>{run.log.slice(-400).join("\n")}</pre>
    </div>
  </details>
</div>

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
    max-width: 760px;
    margin: 0 auto;
    display: grid;
    gap: 16px;
  }

  .status {
    display: grid;
    gap: 14px;
    padding: 18px 20px;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: var(--panel);
  }

  .status.good {
    border-color: var(--green);
  }

  .status.bad {
    border-color: var(--bad-line);
    background: var(--bad-bg);
  }

  .top {
    display: flex;
    align-items: flex-start;
    gap: 16px;
  }

  .now {
    flex: 1;
    min-width: 0;
    display: grid;
    gap: 4px;
  }

  .eyebrow {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--muted);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.02em;
  }

  h2 {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 20px;
    font-weight: 650;
    letter-spacing: -0.3px;
    overflow-wrap: anywhere;
  }

  .detail {
    color: var(--blue-text);
    font-size: 13px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .stop {
    flex-shrink: 0;
  }

  .facts {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 16px;
    color: var(--muted);
    font-size: 12px;
    font-variant-numeric: tabular-nums;
  }

  .percent {
    color: var(--text);
    font-weight: 650;
  }

  .connected {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    margin-left: auto;
  }

  .result {
    display: flex;
    align-items: flex-start;
    gap: 14px;
  }

  .sign {
    width: 40px;
    height: 40px;
    flex-shrink: 0;
    display: grid;
    place-items: center;
    border-radius: 50%;
    background: var(--pill-bg);
    color: var(--muted);
  }

  .sign.good {
    background: var(--green);
    color: var(--bg);
  }

  .sign.bad {
    background: var(--red);
    color: var(--bg);
  }

  .words {
    display: grid;
    gap: 6px;
    min-width: 0;
  }

  .words p {
    color: var(--muted);
    font-size: 13px;
    line-height: 1.55;
  }

  .bad .words p {
    color: var(--bad-text);
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 8px;
  }

  .next {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-right: auto;
  }

  .loose {
    display: flex;
    gap: 6px;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.45;
  }

  .loose.warning {
    color: var(--warn-text);
  }

  .turn {
    display: grid;
    gap: 10px;
    padding: 18px 20px;
    border: 2px solid var(--blue);
    border-radius: 14px;
    background: var(--selected);
    scroll-margin: 16px;
  }

  .turn .eyebrow {
    color: var(--blue-text);
    text-transform: uppercase;
    font-size: 11px;
  }

  .turn h3 {
    font-size: 17px;
    font-weight: 650;
  }

  .ask-text {
    font-size: 13px;
    line-height: 1.55;
  }

  .field {
    display: grid;
    gap: 5px;
    font-size: 12px;
    color: var(--muted);
  }

  .field input {
    padding: 8px 10px;
    border-radius: 6px;
    border: 1px solid var(--line);
    background: var(--inset);
    color: var(--text);
  }

  .list {
    padding: 6px;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: var(--chrome);
  }

  .wait {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 4px;
  }

  .log summary {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 4px;
    color: var(--muted);
    font-size: 12px;
    cursor: pointer;
  }

  .log summary:hover {
    color: var(--text);
  }

  .count {
    opacity: 0.7;
  }

  .logbody {
    position: relative;
    margin-top: 6px;
  }

  .copy {
    position: absolute;
    top: 6px;
    right: 6px;
    font-size: 12px;
  }

  .log pre {
    margin: 0;
    max-height: 300px;
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
  }

  .dfoot {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 20px;
    border-top: 1px solid var(--line);
  }

  @media (max-width: 420px) {
    .top {
      flex-direction: column;
    }

    .connected {
      margin-left: 0;
    }
  }
</style>
