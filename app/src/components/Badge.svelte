<!-- SPDX-License-Identifier: Unlicense -->
<!-- A small round icon that says one thing in its tooltip: an update, a checkout, a write to the headset, a
     reason a workflow cannot run. A button that does nothing, so the tooltip shows from the keyboard too; inside
     another button (focusable false) a plain image with the same words. -->
<script lang="ts">
  import Icon, { type IconName } from "./Icon.svelte";

  interface Props {
    icon: IconName;
    tip: string;
    tone?: "" | "warn" | "good" | "info" | "bad";
    /** Where the tooltip opens: "end" keeps it inside a tile's right edge. */
    place?: "" | "end" | "below";
    /** False inside another button, which may not hold one. */
    focusable?: boolean;
  }

  let { icon, tip, tone = "", place = "", focusable = true }: Props = $props();
</script>

{#if focusable}
  <button
    type="button"
    class="badge {tone}"
    class:tip-end={place === "end"}
    class:tip-below={place === "below"}
    data-tip={tip}
    aria-label={tip}><Icon name={icon} size={13} /></button
  >
{:else}
  <span
    class="badge {tone}"
    class:tip-end={place === "end"}
    class:tip-below={place === "below"}
    data-tip={tip}
    role="img"
    aria-label={tip}><Icon name={icon} size={13} /></span
  >
{/if}

<style>
  .badge {
    width: 24px;
    height: 24px;
    flex-shrink: 0;
    border-radius: 50%;
    display: inline-grid;
    place-items: center;
    background: var(--pill-bg);
    color: var(--pill-text);
    cursor: default;
    padding: 0;
  }

  .warn {
    background: var(--update-bg);
    color: var(--update-text);
  }

  .good {
    background: var(--good-bg);
    color: var(--good-text);
  }

  .info {
    background: var(--blue-bg);
    color: var(--blue-text);
  }

  .bad {
    background: var(--bad-bg);
    color: var(--bad-text);
  }
</style>
