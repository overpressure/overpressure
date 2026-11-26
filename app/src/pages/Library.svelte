<!-- SPDX-License-Identifier: Unlicense -->
<!-- The headsets on this computer as tiles, and + to add one from the catalogue. -->
<script lang="ts">
  import HeadsetArt from "../components/HeadsetArt.svelte";
  import Icon from "../components/Icon.svelte";
  import type { HeadsetEntry } from "../lib/protocol";

  let { headsets, onopen }: { headsets: HeadsetEntry[]; onopen: (h: HeadsetEntry) => void } = $props();

  const here = $derived(headsets.filter((h) => h.downloaded));
  let dialog = $state<HTMLDialogElement>();
</script>

<h1>Headsets</h1>

<div class="cards">
  {#each here as h (h.codename)}
    <button class="tile" onclick={() => onopen(h)} disabled={Boolean(h.refused)} title={h.refused || undefined}>
      {#if h.refused}<span class="pill bad">Needs a newer overpressure</span>{/if}
      <div class="art"><HeadsetArt name={h.name} width={150} /></div>
      <div>
        <h3>{h.name}</h3>
        <p class="muted">{h.downloaded}{h.channel ? ` · ${h.channel}` : ""}</p>
      </div>
    </button>
  {/each}
  <button class="tile add" title="Add a headset" aria-label="Add a headset" onclick={() => dialog?.showModal()}>
    <Icon name="plus" size={28} />
  </button>
</div>
{#if here.length === 0}<p class="muted hint">No headset on this computer yet. Choose + to add one.</p>{/if}

<dialog bind:this={dialog} aria-labelledby="add-title">
  <div class="title">
    <h2 id="add-title">Add a headset</h2>
    <button class="iconbtn" aria-label="Close" onclick={() => dialog?.close()}><Icon name="close" /></button>
  </div>
  <div class="list">
    {#each headsets as h (h.codename)}
      <div class="option">
        <div class="thumb"><HeadsetArt name={h.name} width={64} /></div>
        <div class="text">
          <h3>{h.name}</h3>
          <p class="muted">{h.soc || h.repository}</p>
        </div>
        {#if h.downloaded}<span class="pill good">On this computer</span>{:else}<span class="pill">Not downloaded</span>{/if}
      </div>
    {/each}
  </div>
  <p class="foot muted">
    Downloading from here comes next. Until then:
    <code>python -m overpressure headset add CODENAME</code>
  </p>
</dialog>

<style>
  h1 {
    margin-bottom: 20px;
  }

  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 16px;
  }

  .tile {
    position: relative;
    aspect-ratio: 1.04;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    padding: 16px;
    text-align: left;
    border-radius: 12px;
    border: 1px solid var(--line);
    background: var(--panel);
    transition:
      background 0.12s,
      border-color 0.12s;
  }

  .tile:hover:not(:disabled) {
    background: var(--hover);
    border-color: var(--blue);
  }

  .tile .pill {
    position: absolute;
    top: 10px;
    right: 10px;
  }

  .tile p {
    font-size: 12px;
    margin-top: 3px;
  }

  .art {
    flex: 1;
    display: grid;
    place-items: center;
    margin: 16px 0 8px;
  }

  .add {
    align-items: center;
    justify-content: center;
    background: transparent;
    border-style: dashed;
    color: var(--muted);
  }

  .add:hover {
    color: var(--blue);
  }

  .hint {
    margin-top: 16px;
    font-size: 12px;
  }

  .title {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 16px 20px 12px;
  }

  .list {
    padding: 0 12px 12px;
  }

  .option {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 8px;
    border-radius: 8px;
  }

  .option .text {
    flex: 1;
  }

  .option p {
    font-size: 12px;
    margin-top: 2px;
  }

  .thumb {
    width: 72px;
    display: grid;
    place-items: center;
  }

  .foot {
    border-top: 1px solid var(--line);
    padding: 12px 20px;
    font-size: 12px;
  }

  code {
    font-size: 11px;
  }

  @media (max-width: 680px) {
    .cards {
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
  }
</style>
