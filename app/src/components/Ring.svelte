<!-- SPDX-License-Identifier: Unlicense -->
<!-- The run's ring: one segment per stage (done green, active blue, failed red, the rest the track), each stage's
     name beside its segment, and in the middle the share done and the stage, or the run's end. It moves only with
     the engine's progress; a tween smooths the steps between events. -->
<script lang="ts">
  import { Tween } from "svelte/motion";
  import { cubicOut } from "svelte/easing";
  import type { StageStatus } from "../lib/run";

  interface Props {
    /** Each stage's name and status, in the plan's order. */
    stages: { title: string; status: StageStatus }[];
    /** The whole run's share done, 0 to 1. */
    fraction: number;
    /** The stage the run is in (0-based). */
    active: number;
    /** The run's end: the ring shows it in the middle. */
    outcome: "done" | "failed" | "stopped" | null;
  }

  let { stages, fraction, active, outcome }: Props = $props();

  const SIZE = 400;
  const C = SIZE / 2;
  const R = 128;
  const GAP = 4; // degrees between two segments
  const LABEL = R + 30; // where a stage's name sits

  const reduced = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const shown = new Tween(0, { duration: reduced ? 0 : 450, easing: cubicOut });
  $effect(() => {
    shown.target = outcome === "done" ? 1 : fraction;
  });

  function point(degrees: number, radius = R): [number, number] {
    const a = (degrees * Math.PI) / 180;
    return [C + radius * Math.sin(a), C - radius * Math.cos(a)];
  }

  function arc(from: number, to: number): string {
    const [x1, y1] = point(from);
    const [x2, y2] = point(to);
    return `M ${x1} ${y1} A ${R} ${R} 0 ${to - from > 180 ? 1 : 0} 1 ${x2} ${y2}`;
  }

  const segments = $derived(
    stages.map((stage, i) => {
      const from = (i / stages.length) * 360 + GAP / 2;
      const to = ((i + 1) / stages.length) * 360 - GAP / 2;
      const reached = Math.min(to, Math.max(from, shown.current * 360));
      const status = stage.status;
      const colour = status === "failed" ? "var(--red)" : status === "done" ? "var(--green)" : "var(--blue)";
      return { from, to, colour, filled: status === "done" ? to : reached };
    }),
  );

  // Each name beside the middle of its segment: on the right it starts there, on the left it ends there, at the
  // top and the bottom it is centred above or below.
  const labels = $derived(
    stages.map((stage, i) => {
      const middle = ((i + 0.5) / stages.length) * 360;
      const [x, y] = point(middle, LABEL);
      const sin = Math.sin((middle * Math.PI) / 180);
      const cos = Math.cos((middle * Math.PI) / 180);
      const side = sin > 0.35 ? "right" : sin < -0.35 ? "left" : cos > 0 ? "top" : "bottom";
      return { title: stage.title, status: stage.status, x: (x / SIZE) * 100, y: (y / SIZE) * 100, side };
    }),
  );

  const percent = $derived(Math.min(outcome === "done" ? 100 : 99, Math.round(shown.current * 100)));
</script>

<div class="ring" role="img" aria-label="{percent}% done. Stage {active + 1} of {stages.length}: {stages[active]?.title ?? ''}">
  <div class="area">
    <svg viewBox="0 0 {SIZE} {SIZE}" aria-hidden="true">
      {#each segments as s, i (i)}
        <path d={arc(s.from, s.to)} fill="none" stroke="var(--track)" stroke-width="20" />
        {#if s.filled > s.from}
          <path d={arc(s.from, s.filled)} fill="none" stroke={s.colour} stroke-width="20" />
        {/if}
      {/each}
      {#if outcome === null}
        <text x={C} y={C + 12} text-anchor="middle" class="percent">{percent}%</text>
        <text x={C} y={C + 44} text-anchor="middle" class="stage">Stage {active + 1} of {stages.length}</text>
      {:else}
        <g class="end {outcome}">
          <circle cx={C} cy={C - 14} r="36" />
          {#if outcome === "done"}
            <path d="M{C - 17} {C - 14}l12 12 23-24" class="mark" />
          {:else if outcome === "failed"}
            <path d="M{C} {C - 34}v24m0 13v.5" class="mark" />
          {:else}
            <path d="M{C - 12} {C - 26}h24v24h-24z" class="mark" />
          {/if}
          <text x={C} y={C + 46} text-anchor="middle" class="word"
            >{outcome === "done" ? "Completed" : outcome === "failed" ? "Failed" : "Stopped"}</text
          >
        </g>
      {/if}
    </svg>
    {#each labels as l, i (i)}
      <span class="label {l.side} {l.status}" class:current={i === active && outcome === null} style:left="{l.x}%" style:top="{l.y}%"
        >{l.title}</span
      >
    {/each}
  </div>
</div>

<style>
  .ring {
    padding: 28px 104px;
  }

  .area {
    position: relative;
    width: 100%;
    max-width: 320px;
    margin: 0 auto;
  }

  svg {
    display: block;
    width: 100%;
    height: auto;
  }

  .percent {
    font-size: 50px;
    font-weight: 650;
    fill: var(--text);
    font-variant-numeric: tabular-nums;
  }

  .stage {
    font-size: 15px;
    font-weight: 550;
    fill: var(--muted);
  }

  .label {
    position: absolute;
    width: max-content;
    max-width: 104px;
    font-size: 12px;
    line-height: 1.3;
    color: var(--muted);
  }

  .label.right {
    transform: translate(0, -50%);
  }

  .label.left {
    transform: translate(-100%, -50%);
    text-align: right;
  }

  .label.top {
    transform: translate(-50%, -100%);
    text-align: center;
  }

  .label.bottom {
    transform: translate(-50%, 0);
    text-align: center;
  }

  .label.done {
    color: var(--good);
  }

  .label.failed {
    color: var(--red);
  }

  .label.current {
    color: var(--text);
    font-weight: 650;
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
    font-size: 14px;
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

  @media (max-width: 860px) {
    .ring {
      padding-top: 12px;
      padding-bottom: 12px;
    }
  }

  @media (max-width: 520px) {
    .ring {
      padding: 12px 84px;
    }

    .label {
      max-width: 80px;
      font-size: 11px;
    }
  }
</style>
