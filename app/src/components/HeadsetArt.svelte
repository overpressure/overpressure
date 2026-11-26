<!-- SPDX-License-Identifier: Unlicense -->
<!-- A placeholder until each headset's front illustration comes from overpressure/brand: a plain visor. -->
<script lang="ts">
  let { name, width = 180 }: { name: string; width?: number } = $props();
  const id = $props.id();
</script>

<svg class="art" {width} viewBox="0 0 260 158" role="img" aria-label="{name} (placeholder illustration)">
  <defs>
    <linearGradient id="visor-{id}" x1="0" y1="0" x2=".2" y2="1">
      <stop offset="0" stop-color="#4b5867" /><stop offset=".4" stop-color="#232c37" /><stop offset="1" stop-color="#0b1018" />
    </linearGradient>
  </defs>
  <ellipse cx="130" cy="143" rx="93" ry="8" fill="currentColor" opacity=".18" />
  <path d="M42 76Q26 22 82 15q48-9 98 5 46 10 40 56" fill="none" stroke="var(--track)" stroke-width="11" />
  <rect x="20" y="68" width="34" height="31" rx="12" fill="var(--track)" />
  <rect x="206" y="68" width="34" height="31" rx="12" fill="var(--track)" />
  <path
    d="M52 42q78-11 156 0 25 6 27 31l-6 36q-3 20-25 23-77 10-150 0-22-3-25-23l-6-36q2-25 29-31Z"
    fill="var(--surface)"
    stroke="var(--line)"
    stroke-width="2"
  />
  <path
    d="M56 50q74-10 148 0 18 3 19 24l-5 28q-2 16-20 19-71 9-136 0-18-3-20-19l-5-28q1-21 19-24Z"
    fill="url(#visor-{id})"
  />
  <path d="M60 64q70-14 140 0" fill="none" stroke="var(--blue)" stroke-opacity=".35" stroke-width="2" />
</svg>

<style>
  .art {
    display: block;
    height: auto;
    max-width: 100%;
    color: var(--text);
  }
</style>
