<!-- SPDX-License-Identifier: Unlicense -->
<!-- A run's stages as a list down the page, in order, each with its mark (done, under way, failed, or its number)
     and how long it takes. The stage under way, or the one that failed, is open: its tasks, each with what it does
     now, its own bar when how far it is is known, and the notes it left. Any other stage opens with a click. -->
<script lang="ts">
  import Badge from "./Badge.svelte";
  import Icon from "./Icon.svelte";
  import Spinner from "./Spinner.svelte";
  import type { RunState, StageState, TaskState } from "../lib/run";
  import { duration, stageLeft, stageStatus } from "../lib/run";

  interface Props {
    run: RunState;
    /** The stage the run is in (0-based). */
    active: number;
  }

  let { run, active }: Props = $props();

  /** Stages opened or closed by the owner, by id; the others follow the run. */
  let toggled = $state<Record<string, boolean>>({});

  const ended = $derived(run.finished !== null);

  function open(stage: StageState, index: number): boolean {
    const status = stageStatus(stage);
    const follows = status === "failed" || (!ended && index === active);
    return toggled[stage.id] ?? follows;
  }

  function meta(stage: StageState, index: number): string {
    const status = stageStatus(stage);
    if (status === "done") return stage.tasks.every((t) => t.status === "skipped") ? "Already done" : "Done";
    if (status === "failed") return "Failed";
    if (status === "active" && !ended) return duration(stageLeft(stage)) ? `${duration(stageLeft(stage))} left` : "";
    if (ended) return status === "active" ? "Stopped" : "Not run";
    return duration(stageLeft(stage));
  }

  function taskState(task: TaskState): string {
    if (task.status === "running") return run.ask ? "Waiting for you" : task.progress || "Working…";
    if (task.status === "done") return "Done";
    if (task.status === "skipped") return "Already there";
    if (task.status === "failed") return "Failed";
    return ended ? "Not run" : task.estimate;
  }

  const notesOf = (task: TaskState) => run.notes.filter((n) => n.task === task.id);
</script>

<ol class="stages">
  {#each run.stages as stage, i (stage.id)}
    {@const status = stageStatus(stage)}
    {@const isOpen = open(stage, i)}
    {@const current = !ended && i === active && status !== "done"}
    <li class="stage {status}" class:current>
      <span class="rail" aria-hidden="true"></span>
      <button
        class="head"
        aria-expanded={isOpen}
        aria-controls="stage-{stage.id}"
        onclick={() => (toggled[stage.id] = !isOpen)}
      >
        <span class="mark">
          {#if status === "done"}<Icon name="check" size={14} />
          {:else if status === "failed"}<Icon name="alert" size={14} />
          {:else if current}<Spinner size={14} />
          {:else}{i + 1}{/if}
        </span>
        <span class="title">{stage.title}</span>
        {#if stage.tasks.some((t) => t.writes) && status !== "done"}
          <Badge icon="alert" tone="warn" tip="Writes to the headset" focusable={false} />
        {/if}
        <span class="meta">{meta(stage, i)}</span>
        <span class="chevron" class:turned={isOpen}><Icon name="right" size={14} /></span>
      </button>

      {#if isOpen}
        <div class="body" id="stage-{stage.id}">
          {#if stage.summary}<p class="summary">{stage.summary}</p>{/if}
          <ul class="tasks">
            {#each stage.tasks as task (task.id)}
              <li class="task {task.status}">
                <span class="tmark" aria-hidden="true">
                  {#if task.status === "running"}<Spinner size={12} />
                  {:else if task.status === "done" || task.status === "skipped"}<Icon name="check" size={12} />
                  {:else if task.status === "failed"}<Icon name="alert" size={12} />
                  {:else}<span class="dot"></span>{/if}
                </span>
                <div class="tmain">
                  <div class="tline">
                    <span class="tname">{task.title}</span>
                    {#if task.writes}<Badge icon="alert" tone="warn" tip="Writes to the headset" />{/if}
                    <span class="tstate">{taskState(task)}</span>
                  </div>
                  {#if task.status === "running" && task.fraction !== null}
                    <div class="tbar"><span style:width="{task.fraction * 100}%"></span></div>
                  {/if}
                  {#each notesOf(task) as note, n (n)}
                    <p class="note" class:warning={note.level === "warning"}>
                      <Icon name={note.level === "warning" ? "alert" : "info"} size={13} />{note.text}
                    </p>
                  {/each}
                </div>
              </li>
            {/each}
          </ul>
        </div>
      {/if}
    </li>
  {/each}
</ol>

<style>
  .stages {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
  }

  .stage {
    position: relative;
    display: grid;
    padding-bottom: 4px;
  }

  /* The line between the marks, from this stage's mark down to the next one's. */
  .rail {
    position: absolute;
    left: 21px;
    top: 34px;
    bottom: -4px;
    width: 2px;
    border-radius: 1px;
    background: var(--track);
  }

  .stage:last-child .rail {
    display: none;
  }

  .stage.done .rail {
    background: var(--green);
  }

  .head {
    display: flex;
    align-items: center;
    gap: 12px;
    width: 100%;
    min-height: 44px;
    padding: 6px 10px;
    border-radius: 10px;
    text-align: left;
    color: var(--text);
  }

  .head:hover {
    background: var(--hover);
  }

  .mark {
    position: relative;
    z-index: 1;
    width: 24px;
    height: 24px;
    flex-shrink: 0;
    display: grid;
    place-items: center;
    border-radius: 50%;
    border: 2px solid var(--track);
    background: var(--bg);
    color: var(--muted);
    font-size: 12px;
    font-weight: 650;
    font-variant-numeric: tabular-nums;
  }

  .done .mark {
    border-color: var(--green);
    background: var(--green);
    color: var(--bg);
  }

  .current .mark {
    border-color: var(--blue);
    background: var(--blue-bg);
  }

  .failed .mark {
    border-color: var(--red);
    background: var(--bad-bg);
    color: var(--red);
  }

  .title {
    font-weight: 600;
  }

  .pending:not(.current) .title {
    color: var(--muted);
  }

  .meta {
    margin-left: auto;
    color: var(--muted);
    font-size: 12px;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .failed .meta {
    color: var(--red);
  }

  .chevron {
    display: grid;
    color: var(--muted);
    transition: transform 0.15s ease;
  }

  .chevron.turned {
    transform: rotate(90deg);
  }

  .body {
    margin: 2px 0 8px 46px;
    display: grid;
    gap: 6px;
  }

  .summary {
    color: var(--muted);
    font-size: 12px;
    line-height: 1.5;
  }

  .tasks {
    list-style: none;
    margin: 0;
    padding: 6px 0;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--panel);
  }

  .task {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 7px 12px;
  }

  .task.running {
    background: var(--selected);
  }

  .tmark {
    width: 16px;
    height: 20px;
    flex-shrink: 0;
    display: grid;
    place-items: center;
    color: var(--muted);
  }

  .done .tmark,
  .skipped .tmark {
    color: var(--green);
  }

  .task.failed .tmark {
    color: var(--red);
  }

  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--track);
  }

  .tmain {
    flex: 1;
    min-width: 0;
    display: grid;
    gap: 6px;
  }

  .tline {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 20px;
  }

  .tname {
    font-size: 13px;
  }

  .task.pending .tname {
    color: var(--muted);
  }

  .task.running .tname {
    font-weight: 600;
  }

  .tstate {
    margin-left: auto;
    color: var(--muted);
    font-size: 12px;
    font-variant-numeric: tabular-nums;
    text-align: right;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 55%;
  }

  .task.running .tstate {
    color: var(--blue-text);
  }

  .task.failed .tstate {
    color: var(--red);
  }

  .tbar {
    height: 4px;
    border-radius: 2px;
    background: var(--track);
    overflow: hidden;
  }

  .tbar span {
    display: block;
    height: 100%;
    background: var(--blue);
    transition: width 0.3s ease;
  }

  .note {
    display: flex;
    gap: 6px;
    align-items: flex-start;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.45;
  }

  .note.warning {
    color: var(--warn-text);
  }

  .note :global(svg) {
    flex-shrink: 0;
    margin-top: 1px;
  }

  @media (prefers-reduced-motion: reduce) {
    .chevron,
    .tbar span {
      transition: none;
    }
  }
</style>
