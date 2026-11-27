<!-- SPDX-License-Identifier: Unlicense -->
<!-- A headset's picture: its icon from overpressure/brand (matrix/<codename>.svg, copied unchanged into
     assets/headsets/), the headset from the front on the dark tile. A headset without one gets a plain tile. -->
<script lang="ts">
  const ICONS = import.meta.glob<string>("../assets/headsets/*.svg", { eager: true, query: "?url", import: "default" });

  let { codename, name, size = 96 }: { codename: string; name: string; size?: number } = $props();

  const src = $derived(ICONS[`../assets/headsets/${codename}.svg`]);
</script>

{#if src}
  <img class="art" {src} alt={name} width={size} height={size} draggable="false" />
{:else}
  <svg class="art" width={size} height={size} viewBox="0 0 512 512" role="img" aria-label={name}>
    <rect width="512" height="512" rx="116" fill="#25282f" />
    <rect x="104" y="184" width="304" height="164" rx="74" fill="#3b4048" />
  </svg>
{/if}

<style>
  .art {
    display: block;
    flex-shrink: 0;
    max-width: 100%;
    height: auto;
    border-radius: 22.6%;
    box-shadow: 0 6px 18px var(--shadow);
    user-select: none;
  }
</style>
