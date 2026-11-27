<!-- SPDX-License-Identifier: Unlicense -->
<!-- The headsets on this computer as tiles (a newer release marked), a search once there are several, and +
     to add one from the catalogue. -->
<script lang="ts">
  import { app } from "../lib/app.svelte";
  import AddDialog from "../components/AddDialog.svelte";
  import Badge from "../components/Badge.svelte";
  import HeadsetArt from "../components/HeadsetArt.svelte";
  import Icon from "../components/Icon.svelte";
  import { versionLine } from "../lib/words";

  let adding = $state(false);
  let search = $state("");
  let field = $state<HTMLInputElement>();

  const here = $derived(app.headsets.filter((h) => h.downloaded));
  const shown = $derived(
    here.filter((h) => `${h.name} ${h.codename}`.toLowerCase().includes(search.trim().toLowerCase())),
  );

  function keys(event: KeyboardEvent): void {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k" && !adding) {
      event.preventDefault();
      field?.focus();
    }
  }
</script>

<svelte:window onkeydown={keys} />

{#if here.length > 1}
  <label class="search">
    <Icon name="search" />
    <input bind:this={field} bind:value={search} placeholder="Search your headsets…" aria-label="Search your headsets" />
    <span class="key">Ctrl K</span>
  </label>
{/if}

<div class="cards">
  {#each shown as h (h.codename)}
    <div class="cell">
      <button class="tile" onclick={() => app.open(h.codename)} disabled={Boolean(h.refused)} aria-label="Open the {h.name}">
        <div class="art"><HeadsetArt codename={h.codename} name={h.name} size={88} /></div>
        <div>
          <h3>{h.name}</h3>
          <p class="muted">{versionLine(h.downloaded, h.channel)}</p>
        </div>
      </button>
      <span class="badges">
        {#if h.refused}<Badge icon="alert" tone="bad" tip={`Needs a newer overpressure: ${h.refused}`} />
        {:else if app.updates[h.codename]}<Badge icon="up" tone="warn" tip={`Update available: ${app.updates[h.codename]}`} />{/if}
      </span>
    </div>
  {/each}
  <button class="tile add" title="Add a headset" aria-label="Add a headset" onclick={() => (adding = true)}>
    <Icon name="plus" size={28} />
  </button>
</div>
{#if here.length === 0}
  <p class="muted hint">No headset on this computer yet. Choose + to add one.</p>
{:else if shown.length === 0}
  <p class="muted hint">No headset matches your search.</p>
{/if}

{#if adding}<AddDialog onclose={() => (adding = false)} />{/if}

<style>
  .search {
    display: flex;
    align-items: center;
    gap: 9px;
    height: 40px;
    padding: 0 12px;
    margin-bottom: 20px;
    border-radius: 8px;
    border: 1px solid var(--line);
    background: var(--inset);
    color: var(--muted);
  }

  .search:focus-within {
    border-color: var(--blue);
  }

  .search input {
    flex: 1;
    min-width: 0;
    border: 0;
    outline: none;
    background: transparent;
    color: var(--text);
  }

  .key {
    font-size: 11px;
    border: 1px solid var(--line);
    border-radius: 4px;
    padding: 1px 5px;
  }

  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(168px, 200px));
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

  .cell {
    position: relative;
    display: grid;
  }

  .badges {
    position: absolute;
    top: 10px;
    right: 10px;
    display: flex;
    gap: 4px;
  }

  .badges:empty {
    display: none;
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

  @media (max-width: 680px) {
    .cards {
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
  }
</style>
