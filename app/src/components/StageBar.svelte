<!-- SPDX-License-Identifier: Unlicense -->
<!-- How far a run is, as one bar split into its stages: each stage its equal part, filled as far as it is (done
     green, under way blue, failed red). Its stages' names are in the list below it; the bar's label says it all for
     a screen reader. -->
<script lang="ts">
  import type { StageStatus } from "../lib/run";

  interface Props {
    /** Each stage's status and share done (0 to 1), in the plan's order. */
    stages: { title: string; status: StageStatus; fraction: number }[];
    /** What the bar says to a screen reader: "34% done, stage 2 of 4". */
    label: string;
  }

  let { stages, label }: Props = $props();
</script>

<div class="bar" role="img" aria-label={label}>
  {#each stages as s, i (i)}
    <span class="segment {s.status}" title={s.title}>
      <span class="fill" style:width="{(s.status === 'done' ? 1 : s.fraction) * 100}%"></span>
    </span>
  {/each}
</div>

<style>
  .bar {
    display: flex;
    gap: 4px;
    height: 8px;
  }

  .segment {
    flex: 1;
    min-width: 0;
    border-radius: 4px;
    background: var(--track);
    overflow: hidden;
  }

  .fill {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: var(--blue);
    transition: width 0.45s cubic-bezier(0.22, 1, 0.36, 1);
  }

  .done .fill {
    background: var(--green);
  }

  .failed .fill {
    background: var(--red);
    min-width: 8px;
  }

  @media (prefers-reduced-motion: reduce) {
    .fill {
      transition: none;
    }
  }
</style>
