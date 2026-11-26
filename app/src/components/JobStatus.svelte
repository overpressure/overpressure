<!-- SPDX-License-Identifier: Unlicense -->
<!-- A job's state in one line: its bar while it runs (to and fro while how far is unknown), then its result. -->
<script lang="ts">
  import { jobLine, type JobState } from "../lib/job";

  let { job }: { job: JobState } = $props();
  const failed = $derived(job.code !== null && job.code !== 0);
</script>

<div class="status" class:failed role="status">
  {#if job.code === null}
    <div class="bar" class:unknown={job.fraction === null}>
      <span style:width="{(job.fraction ?? 0.3) * 100}%"></span>
    </div>
  {/if}
  <p title={jobLine(job)}>{jobLine(job)}</p>
</div>

<style>
  .status {
    display: grid;
    gap: 6px;
    min-width: 0;
  }

  p {
    font-size: 12px;
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .failed p {
    color: var(--red);
    white-space: normal;
  }

  .bar {
    height: 6px;
    border-radius: 3px;
    background: var(--track);
    overflow: hidden;
  }

  .bar span {
    display: block;
    height: 100%;
    border-radius: 3px;
    background: var(--blue);
    transition: width 0.3s ease;
  }

  .bar.unknown span {
    animation: fro 1.4s ease-in-out infinite alternate;
  }

  @keyframes fro {
    from {
      transform: translateX(0);
    }
    to {
      transform: translateX(233%);
    }
  }
</style>
