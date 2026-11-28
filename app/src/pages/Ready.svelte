<!-- SPDX-License-Identifier: Unlicense -->
<!-- A workflow chosen, before it runs: the run page as it will look, ready to start. The status card says what the
     workflow does, or how and when its last run ended (failed and why, stopped, cut off, finished), how far that run
     came (the run's bar) and what is left, with one button: Start, Continue, or run it again when every stage is
     done. Below, the stages as that run left them, the next one open; a stage it reached offers "Run again from
     here". What is done is not done again unless its result is gone: each task's done check decides. -->
<script lang="ts">
  import Icon from "../components/Icon.svelte";
  import StageBar from "../components/StageBar.svelte";
  import StageList from "../components/StageList.svelte";
  import { app, type Ready } from "../lib/app.svelte";
  import type { RunState } from "../lib/run";
  import { duration, overall, runLeft, stageFraction, stageStatus, when } from "../lib/run";

  let { ready }: { ready: Ready } = $props();

  const workflow = $derived(app.headset?.workflows.find((w) => w.key === ready.workflow));
  const shown = $derived<RunState>({
    run: "",
    workflow: ready.workflow,
    headset: app.headset?.name ?? "",
    stages: ready.stages.map((s) => ({ ...s, tasks: s.tasks.map((t) => ({ ...t, fraction: null, progress: "" })) })),
    notes: [],
    ask: null,
    log: [],
    finished: null,
    exited: false,
  });
  const tasks = $derived(shown.stages.flatMap((s) => s.tasks));
  const statuses = $derived(shown.stages.map((s) => stageStatus(s)));
  const done = $derived(statuses.filter((s) => s === "done").length);
  const complete = $derived(statuses.length > 0 && done === statuses.length);
  const begun = $derived(tasks.some((t) => t.status !== "pending"));
  const reached = $derived(statuses.some((s) => s !== "pending"));
  const now = $derived(overall(shown));
  const next = $derived(shown.stages[now.stage]);
  const left = $derived(duration(runLeft(shown)));
  const bar = $derived(
    shown.stages.map((s, i) => ({ title: s.title, status: statuses[i] ?? "pending", fraction: stageFraction(s) })),
  );
  const earlier = $derived(ready.earlier);
  const at = $derived(earlier ? when(earlier.at) : "");
  const cut = $derived(tasks.find((t) => t.status === "running"));
  const where = $derived(tasks.find((t) => t.id === earlier?.task)?.title ?? cut?.title ?? "");

  const cap = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

  /** What the card says first: how the last run ended, or what the workflow is. */
  const said = $derived.by((): { tone: "" | "good" | "bad"; title: string; text: string } => {
    const inTask = where ? `, in “${where}”` : "";
    if (complete) {
      const finished = earlier?.status === "done" && at ? `It finished ${at}.` : "Every stage is done.";
      return { tone: "good", title: "Done", text: `${finished} Running it again runs every stage; what is still on the headset is skipped.` };
    }
    if (earlier?.status === "failed") return { tone: "bad", title: "The last run failed", text: `${cap(at)}${inTask}.` };
    if (earlier?.status === "stopped") return { tone: "", title: "The last run stopped", text: `${cap(at)}${inTask}.` };
    if (earlier?.status === "running" || (begun && !earlier))
      return {
        tone: "",
        title: "The last run did not finish",
        text: `${at ? `${cap(at)}: ` : ""}overpressure or the computer closed during it${where ? ` (“${where}”)` : ""}.`,
      };
    return { tone: "", title: ready.title, text: workflow?.summary ?? "" };
  });

  // After a failure the engine's words say "Press Retry": the button says so too.
  const primary = $derived(
    complete ? `${ready.title} again` : earlier?.status === "failed" ? "Retry" : begun ? "Continue" : "Start",
  );
  const busy = $derived(app.running || app.jobRunning);

  let button = $state<HTMLButtonElement>();
  $effect(() => {
    button?.focus({ preventScroll: true });
  });

  function go(): void {
    void app.begin(ready.workflow, complete ? (ready.stages[0]?.id ?? "") : "");
  }
</script>

<div class="ready">
  <section class="status" class:good={said.tone === "good"} class:bad={said.tone === "bad"}>
    <div class="head">
      {#if said.tone}
        <span class="sign {said.tone}"><Icon name={said.tone === "good" ? "check" : "alert"} size={20} /></span>
      {/if}
      <div class="words">
        <p class="eyebrow">{app.headset?.name ?? "Headset"} · {ready.title}</p>
        <h2>{said.title}</h2>
        {#if said.text}<p class="text">{said.text}</p>{/if}
      </div>
    </div>

    {#if earlier?.error && !complete}
      <div class="why">
        <b>{earlier.error}</b>
        {#if earlier.where}<span>{earlier.where}</span>{/if}
      </div>
    {:else if earlier?.status === "stopped" && earlier.where && !complete}
      <p class="text">{earlier.where}</p>
    {/if}

    {#if begun}
      <StageBar stages={bar} label="{done} of {statuses.length} stages done" />
    {/if}
    <div class="facts">
      {#if begun}
        <span class="strong">{done} of {statuses.length} stages done</span>
        {#if left && !complete}<span>{left} left</span>{/if}
      {:else}
        <span class="strong">{statuses.length} {statuses.length === 1 ? "stage" : "stages"}</span>
        {#if left}<span>{left}</span>{/if}
      {/if}
      {#if workflow?.erases && !begun}
        <span class="erases"><Icon name="alert" size={13} /> Erases the headset</span>
      {/if}
    </div>

    {#if begun && !complete && next}
      <p class="hint">
        {primary} picks up at “{next.title}”. What is done is not done again, unless its result is gone.
      </p>
    {/if}

    <div class="actions">
      {#if reached}<span class="aside">Open a stage to run it again from there.</span>{/if}
      <button class="btn ghost" onclick={() => app.leaveRun()}>Back</button>
      <button class="btn primary" bind:this={button} disabled={busy} onclick={go}>{primary}</button>
    </div>
  </section>

  <section class="list" aria-label="Stages">
    <StageList run={shown} active={now.stage} again={(stage) => void app.begin(ready.workflow, stage)} />
  </section>
</div>

<style>
  .ready {
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
  }

  .head {
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
    gap: 4px;
    min-width: 0;
  }

  .eyebrow {
    color: var(--muted);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.02em;
  }

  h2 {
    font-size: 20px;
    font-weight: 650;
    letter-spacing: -0.3px;
    overflow-wrap: anywhere;
  }

  .text {
    color: var(--muted);
    font-size: 13px;
    line-height: 1.55;
  }

  .why {
    display: grid;
    gap: 4px;
    padding: 10px 12px;
    border-radius: 10px;
    background: var(--bad-bg);
    color: var(--bad-text);
    font-size: 13px;
    line-height: 1.5;
  }

  .why b {
    font-weight: 600;
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

  .strong {
    color: var(--text);
    font-weight: 650;
  }

  .erases {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    color: var(--warn-text);
  }

  .hint {
    color: var(--muted);
    font-size: 12px;
    line-height: 1.5;
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
  }

  .aside {
    margin-right: auto;
    color: var(--muted);
    font-size: 12px;
  }

  @media (max-width: 520px) {
    .aside {
      flex-basis: 100%;
    }
  }

  .list {
    padding: 6px;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: var(--chrome);
  }
</style>
