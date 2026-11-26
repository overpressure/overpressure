<!-- SPDX-License-Identifier: Unlicense -->
<!-- A headset: its facts, and a tile per workflow it offers, each with what it does, whether it erases the
     headset, and why it cannot run now. -->
<script lang="ts">
  import HeadsetArt from "../components/HeadsetArt.svelte";
  import Icon, { type IconName } from "../components/Icon.svelte";
  import type { HeadsetEntry } from "../lib/protocol";

  interface Props {
    headset: HeadsetEntry;
    running: boolean;
    onstart: (workflow: string) => void;
    onshowrun: () => void;
  }

  let { headset, running, onstart, onshowrun }: Props = $props();

  const ICONS: Record<string, IconName> = { install: "download", upgrade: "up", restore: "restore", check: "shield" };
</script>

<section class="hero">
  <div class="art"><HeadsetArt name={headset.name} width={200} /></div>
  <div class="info">
    <h1>{headset.name}</h1>
    <dl>
      <div><dt>Platform</dt><dd>{headset.soc || "—"}</dd></div>
      <div><dt>Support</dt><dd>{headset.downloaded || "not downloaded"}</dd></div>
      <div><dt>Release channel</dt><dd>{headset.channel || "—"}</dd></div>
    </dl>
  </div>
</section>

<h2 class="heading">What do you want to do?</h2>
{#if running}
  <div class="callout info">
    <Icon name="info" />
    <p>A run is under way. <button class="link" onclick={onshowrun}>Show it</button></p>
  </div>
{/if}
<div class="cards">
  {#each headset.workflows as w (w.key)}
    <button
      class="tile"
      class:erases={w.erases}
      disabled={running || Boolean(w.problem)}
      onclick={() => onstart(w.key)}
    >
      <div class="top">
        <span class="badge"><Icon name={ICONS[w.key] ?? "right"} size={22} /></span>
        {#if w.erases}<span class="pill warn">Erases the headset</span>{/if}
      </div>
      <h3>{w.title}</h3>
      <p class="summary">{w.summary}</p>
      {#if w.problem}<p class="problem">{w.problem}</p>{/if}
    </button>
  {/each}
</div>

<style>
  .hero {
    display: flex;
    gap: 32px;
    align-items: center;
    padding: 8px 0 24px;
    border-bottom: 1px solid var(--line);
  }

  .art {
    width: 208px;
    flex-shrink: 0;
    display: grid;
    place-items: center;
  }

  .info {
    flex: 1;
    min-width: 0;
  }

  h1 {
    font-size: 28px;
  }

  dl {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px 20px;
    margin: 16px 0 0;
  }

  dt {
    font-size: 11px;
    color: var(--muted);
    margin-bottom: 3px;
  }

  dd {
    margin: 0;
    font-weight: 550;
  }

  .heading {
    margin: 24px 0 12px;
  }

  .callout {
    margin-bottom: 12px;
    align-items: center;
  }

  .link {
    color: var(--blue);
    padding: 0;
    font-weight: 600;
  }

  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 16px;
  }

  .tile {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 16px;
    text-align: left;
    border-radius: 12px;
    border: 1px solid var(--line);
    background: var(--panel);
    min-height: 170px;
    transition:
      background 0.12s,
      border-color 0.12s;
  }

  .tile:hover:not(:disabled) {
    background: var(--hover);
    border-color: var(--blue);
  }

  .tile:hover:not(:disabled) .badge {
    color: var(--blue);
  }

  .tile:disabled {
    opacity: 1;
  }

  .tile:disabled > :not(.problem) {
    opacity: 0.55;
  }

  .top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 8px;
  }

  .badge {
    width: 44px;
    height: 44px;
    border-radius: 12px;
    background: var(--surface);
    display: grid;
    place-items: center;
  }

  .summary {
    font-size: 12px;
    color: var(--muted);
    display: -webkit-box;
    -webkit-line-clamp: 3;
    line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .problem {
    font-size: 12px;
    color: var(--update-text);
    margin-top: auto;
  }

  @media (max-width: 680px) {
    .hero {
      gap: 16px;
    }

    .art {
      width: 130px;
    }

    dl {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }

  @media (max-width: 460px) {
    .hero {
      flex-direction: column;
      align-items: stretch;
    }
  }
</style>
