<!-- SPDX-License-Identifier: Unlicense -->
<!-- The run's ring: one segment per stage (done green, active blue, failed red, the rest the track), the
     share done in the middle, and a callout on the ring's end naming the stage and the task. It moves only with
     the engine's progress; a tween smooths the steps between events. -->
<script lang="ts">
  import { Tween } from "svelte/motion";
  import { cubicOut } from "svelte/easing";
  import type { StageStatus } from "../lib/run";

  interface Props {
    /** Each stage's status, in the plan's order. */
    stages: StageStatus[];
    /** The whole run's share done, 0 to 1. */
    fraction: number;
    /** The stage the run is in (0-based). */
    active: number;
    task: string;
    detail: string;
    /** The run's end: the ring shows it in the middle and drops the callout. */
    outcome: "done" | "failed" | "stopped" | null;
  }

  let { stages, fraction, active, task, detail, outcome }: Props = $props();

  const CX = 390;
  const CY = 220;
  const R = 114;
  const GAP = 4; // degrees between two segments
  const BOX_W = 210;
  const BOX_H = 66;

  const reduced = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const shown = new Tween(0, { duration: reduced ? 0 : 450, easing: cubicOut });
  $effect(() => {
    shown.target = outcome === "done" ? 1 : fraction;
  });

  function point(degrees: number, radius = R): [number, number] {
    const a = (degrees * Math.PI) / 180;
    return [CX + radius * Math.sin(a), CY - radius * Math.cos(a)];
  }

  function arc(from: number, to: number): string {
    const [x1, y1] = point(from);
    const [x2, y2] = point(to);
    return `M ${x1} ${y1} A ${R} ${R} 0 ${to - from > 180 ? 1 : 0} 1 ${x2} ${y2}`;
  }

  const segments = $derived(
    stages.map((status, i) => {
      const from = (i / stages.length) * 360 + GAP / 2;
      const to = ((i + 1) / stages.length) * 360 - GAP / 2;
      const reached = Math.min(to, Math.max(from, shown.current * 360));
      const colour = status === "failed" ? "var(--red)" : status === "done" ? "var(--green)" : "var(--blue)";
      return { from, to, reached, colour, filled: status === "done" ? to : reached };
    }),
  );

  // The callout sits outside the ring on the end of the progress: a support-function offset keeps the box clear.
  const callout = $derived.by(() => {
    const a = Math.max(GAP / 2, Math.min(360 - GAP / 2, shown.current * 360));
    const dx = Math.sin((a * Math.PI) / 180);
    const dy = -Math.cos((a * Math.PI) / 180);
    const pad = (Math.abs(dx) * BOX_W) / 2 + (Math.abs(dy) * BOX_H) / 2;
    const centre = R + 30 + pad;
    const reach = Math.min(BOX_W / 2 / Math.max(Math.abs(dx), 1e-8), BOX_H / 2 / Math.max(Math.abs(dy), 1e-8));
    const [x, y] = point(a);
    const [lx, ly] = point(a, centre - reach);
    const [bx, by] = point(a, centre);
    return { x, y, lx, ly, bx, by };
  });

  const percent = $derived(Math.min(outcome === "done" ? 100 : 99, Math.round(shown.current * 100)));
</script>

<div class="ring" role="img" aria-label="{percent}% done. Stage {active + 1} of {stages.length}: {task}">
  <svg viewBox="0 0 780 440" aria-hidden="true">
    {#each segments as s, i (i)}
      <path d={arc(s.from, s.to)} fill="none" stroke="var(--track)" stroke-width="22" />
      {#if s.filled > s.from}
        <path d={arc(s.from, s.filled)} fill="none" stroke={s.colour} stroke-width="22" />
      {/if}
    {/each}
    {#if outcome === null}
      <text x={CX} y={CY + 18} text-anchor="middle" class="percent">{percent}%</text>
      <line x1={callout.x} y1={callout.y} x2={callout.lx} y2={callout.ly} stroke="var(--blue)" stroke-width="1.5" />
      <circle cx={callout.x} cy={callout.y} r="4.5" fill="var(--blue)" stroke="var(--panel)" stroke-width="2" />
    {:else}
      <g class="end {outcome}">
        <circle cx={CX} cy={CY - 17} r="37" />
        {#if outcome === "done"}
          <path d="M373 {CY - 17}l12 12 23-24" class="mark" />
        {:else if outcome === "failed"}
          <path d="M390 {CY - 37}v24m0 13v.5" class="mark" />
        {:else}
          <path d="M378 {CY - 29}h24v24h-24z" class="mark" />
        {/if}
        <text x={CX} y={CY + 43} text-anchor="middle" class="word"
          >{outcome === "done" ? "Completed" : outcome === "failed" ? "Failed" : "Stopped"}</text
        >
      </g>
    {/if}
  </svg>
  {#if outcome === null && task}
    <div class="box" style:left="{(callout.bx / 780) * 100}%" style:top="{(callout.by / 440) * 100}%">
      <span class="tag">Stage {active + 1} of {stages.length}</span>
      <b title={task}>{task}</b>
      <span class="detail" title={detail}>{detail}</span>
    </div>
  {/if}
</div>

<style>
  .ring {
    position: relative;
    width: min(100%, 710px);
    margin: 20px auto 8px;
  }

  svg {
    display: block;
    width: 100%;
    height: auto;
    max-height: 340px;
    overflow: visible;
  }

  .percent {
    font-size: 54px;
    font-weight: 650;
    fill: var(--text);
    font-variant-numeric: tabular-nums;
  }

  .end circle {
    fill: var(--green);
  }

  .end.failed circle {
    fill: var(--red);
  }

  .end.stopped circle {
    fill: var(--muted);
  }

  .mark {
    fill: none;
    stroke: var(--panel);
    stroke-width: 6;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  .end.stopped .mark {
    fill: var(--panel);
    stroke-width: 0;
  }

  .word {
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    fill: var(--good);
  }

  .end.failed .word {
    fill: var(--red);
  }

  .end.stopped .word {
    fill: var(--muted);
  }

  .box {
    position: absolute;
    transform: translate(-50%, -50%);
    width: 210px;
    display: grid;
    gap: 1px;
    padding: 8px 11px;
    border-radius: 9px;
    background: var(--callout);
    border: 1px solid var(--callout-line);
    box-shadow: 0 6px 20px var(--shadow);
  }

  .box > * {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .tag {
    font-size: 11px;
    font-weight: 600;
    color: var(--blue);
  }

  .detail {
    font-size: 12px;
    color: var(--muted);
  }
</style>
